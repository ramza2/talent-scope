"""V31 native-first opt-in: PROJECT LLM skipped only on native success."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.prompts.profile_extract import (
    SOURCE_FIRST_V30_PROMPT_VERSION,
    SOURCE_FIRST_V31_PROMPT_VERSION,
    current_profile_prompt,
    resolve_profile_prompt,
)
from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.diff_engine import DiffSpec
from app.modules.analysis.schemas import CreateAnalysisRequest
from app.modules.analysis.service import AnalysisService, InsufficientCandidateError


def _core_candidate(*, projects=None) -> ProfileCandidateDocument:
    return ProfileCandidateDocument.model_validate(
        {
            "profile": {"name": "시험사용자"},
            "jobs": [{"code": "JOB-MGT-PL", "source_refs": []}],
            "expertise": [{"code": "EXP-MGT", "source_refs": []}],
            "skills": [],
            "projects": projects or [],
            "analysis": {"overall_confidence": 0.5},
        }
    )


def test_v31_opt_in_default_false_and_exclusive_with_v30():
    base = {"person_id": uuid4(), "document_ids": [uuid4()]}
    assert CreateAnalysisRequest(**base).source_first_v31 is False
    assert CreateAnalysisRequest(**base).source_first_v30 is False
    assert CreateAnalysisRequest(**base, source_first_v31=True).source_first_v31 is True

    for invalid in ("true", "yes", 1, None):
        with pytest.raises(ValidationError):
            CreateAnalysisRequest(**base, source_first_v31=invalid)

    with pytest.raises(ValidationError, match="동시에"):
        CreateAnalysisRequest(
            **base, source_first_v30=True, source_first_v31=True
        )


def test_v31_prompt_selection_does_not_change_v25_or_v30_defaults():
    service = AnalysisService.__new__(AnalysisService)
    service.settings = SimpleNamespace(analysis_source_first_v30_enabled=False)

    assert service._selected_profile_prompt().prompt_version == "profile-extract-v25"
    assert (
        service._selected_profile_prompt(source_first_v30=True).prompt_version
        == SOURCE_FIRST_V30_PROMPT_VERSION
    )
    assert (
        service._selected_profile_prompt(source_first_v31=True).prompt_version
        == SOURCE_FIRST_V31_PROMPT_VERSION
    )
    with pytest.raises(Exception, match="동시에"):
        service._selected_profile_prompt(
            source_first_v30=True, source_first_v31=True
        )

    v25 = current_profile_prompt()
    v31 = resolve_profile_prompt(SOURCE_FIRST_V31_PROMPT_VERSION)
    assert v31.prompt_version == SOURCE_FIRST_V31_PROMPT_VERSION
    assert v31.core_system_prompt == v25.core_system_prompt
    assert v31.projects_system_prompt == v25.projects_system_prompt


def test_create_analysis_v31_opt_in_is_per_run(monkeypatch):
    person_id, document_id, actor_id = uuid4(), uuid4(), uuid4()
    written, audits, enqueued = [], [], []

    class FakeRepo:
        def get_person(self, value, *, for_update):
            return SimpleNamespace(status="ACTIVE", deleted_at=None)

        def get_manual_create_blocking_run(self, value):
            return None

        def get_profile(self, value, *, for_update):
            return SimpleNamespace(profile_version=1)

        def list_documents_by_ids(self, ids):
            return [(
                SimpleNamespace(
                    processing_status="READY", original_filename="resume.pdf"
                ),
                SimpleNamespace(person_id=person_id),
                "이력서",
            )]

        def create_run(self, **kwargs):
            written.append(kwargs)
            return SimpleNamespace(id=uuid4(), status="QUEUED")

        def add_run_documents(self, run_id, ids):
            return None

        def add_audit(self, **kwargs):
            audits.append(kwargs)

    service = AnalysisService.__new__(AnalysisService)
    service.repo = FakeRepo()
    service.db = SimpleNamespace(commit=lambda: None)
    service.settings = SimpleNamespace(
        analysis_source_first_v30_enabled=False,
        llm_model="test-llm",
        vlm_model="test-vlm",
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis",
        lambda run_id, user_id: enqueued.append((run_id, user_id)),
    )

    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person_id,
            document_ids=[document_id],
            source_first_v31=True,
        ),
        actor_id,
    )
    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person_id,
            document_ids=[document_id],
        ),
        actor_id,
    )

    assert [r["prompt_version"] for r in written] == [
        SOURCE_FIRST_V31_PROMPT_VERSION,
        "profile-extract-v25",
    ]
    assert [a["after"]["source_first_v31_opt_in"] for a in audits] == [True, False]
    assert service._selected_profile_prompt().prompt_version == "profile-extract-v25"


def test_skip_projects_makes_zero_project_llm_and_token_count_calls(monkeypatch):
    """CORE-only path must not invoke PROJECT chat or PROJECT token-count."""
    from app.ai.providers.model_capabilities import ModelCapabilities
    from app.ai.providers.token_count import TokenCountResult
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.core.config import Settings
    import app.modules.analysis.service as svc_mod
    from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot

    def _core_payload() -> dict:
        return {
            "p": {"n": "홍길동", "tg": "EXPERT", "ac": "ABC테크", "cdv": "10년"},
            "j": [{"v": "PL", "c": "JOB-MGT-PL", "t": "PRIMARY"}],
            "s": [{"v": "Python", "c": "TECH-LANG-PYTHON"}],
            "x": [{"v": "사업관리", "c": "EXP-MGT"}],
            "w": [{"co": "ABC테크", "ti": "책임", "s": "2018-01", "e": "2024-12"}],
            "e": [{"sc": "서울대", "mj": "컴공", "dg": "학사"}],
            "c": [{"n": "정보처리기사", "acq": "2010.12"}],
            "conf": 0.9,
        }

    def _doc_block(doc_id: str, *, pages: str) -> str:
        return (
            f"[DOCUMENT]\n"
            f"document_id: {doc_id}\n"
            f"filename: {doc_id[:8]}.pdf\n"
            f"document_type: DOC-PROFILE\n"
            f"content:\n{pages}"
        )

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    chat_phases: list[str] = []
    count_phases: list[str] = []

    class FakeLLM:
        def count_prompt_tokens(self, *, system_prompt, user_prompt, log_context=None):
            phase = (log_context or {}).get("phase")
            count_phases.append(phase)
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=100,
                context_window=10_000,
                status="VERIFIED",
                deployment_id="dep-1",
                route_id="route-1",
                routing_version=1,
            )

        def complete_json(
            self,
            *,
            system_prompt,
            user_prompt,
            log_context=None,
            max_completion_tokens=None,
        ):
            phase = (log_context or {}).get("phase")
            chat_phases.append(phase)
            assert phase == "core"
            return _core_payload()

    monkeypatch.setattr(
        svc_mod,
        "fetch_model_capabilities",
        lambda settings: ModelCapabilities(
            model_id="qwen3-14b",
            context_window=10_000,
            supports_thinking=True,
            supports_tools=True,
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
            status="VERIFIED",
        ),
    )

    settings = Settings(
        llm_base_url="https://gateway.example.test",
        llm_api_key="k",
        llm_model="qwen3-14b",
        analysis_llm_enable_thinking=False,
        analysis_token_count_max_attempts=3,
        analysis_project_split_max_depth=3,
        analysis_project_split_max_calls=8,
        analysis_core_output_ratio=0.25,
        analysis_core_split_max_depth=3,
        analysis_core_split_max_calls=16,
        analysis_project_output_ratio=0.35,
        analysis_token_safety_ratio=0.05,
    )
    repo = MagicMock()
    repo.list_active_codes.return_value = [
        SimpleNamespace(
            code="JOB-MGT-PL", code_type="JOB", name="PL",
            is_active=True, parent_code=None,
        ),
        SimpleNamespace(
            code="TECH-LANG-PYTHON", code_type="TECH", name="Python",
            is_active=True, parent_code=None,
        ),
        SimpleNamespace(
            code="EXP-MGT", code_type="EXP", name="사업관리",
            is_active=True, parent_code=None,
        ),
    ]
    repo.list_aliases_for_codes.return_value = []

    service = AnalysisService.__new__(AnalysisService)
    service.repo = repo
    service.settings = settings
    service.llm = FakeLLM()
    service.db = MagicMock()
    service.storage = MagicMock()

    prompt = resolve_profile_prompt(SOURCE_FIRST_V31_PROMPT_VERSION)
    blocks = _doc_block(
        doc_id,
        pages="[PAGE 1]\n" + ("경력 본문 프로젝트 이력 " * 40),
    )
    docs = (
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_id),
            original_filename="a.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_key="k1",
            preview_storage_key=None,
            document_type_code="DOC-PROFILE",
            document_type_name="프로필",
            version_no=1,
            person_id=__import__("uuid").uuid4(),
            pages=[
                PageSnapshot(
                    page_no=1,
                    extracted_text="경력 본문 프로젝트 이력",
                    layout_json=None,
                )
            ],
        ),
    )
    result = service._extract_staged_compact_candidate(
        prompt=prompt,
        document_blocks=blocks,
        catalog_map={
            "JOB-MGT-PL": ("JOB", True),
            "TECH-LANG-PYTHON": ("TECH", True),
            "EXP-MGT": ("EXP", True),
        },
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): "경력 본문 프로젝트 이력 PL Python 사업관리"},
        documents=docs,
        source_char_count=800,
        base_log={"analysis_run_id": "v31-skip"},
        vlm_pages=0,
        skip_projects=True,
    )

    assert result.projects == []
    assert chat_phases == ["core"]
    assert all(phase == "core" for phase in count_phases)
    assert "projects" not in chat_phases
    assert "projects" not in count_phases


def test_precomputed_core_reuses_core_and_runs_only_project_llm(monkeypatch):
    """Assembly-failure fallback must not re-chat CORE; PROJECT must run."""
    from app.ai.providers.model_capabilities import ModelCapabilities
    from app.ai.providers.token_count import TokenCountResult
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.core.config import Settings
    import app.modules.analysis.service as svc_mod
    from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    chat_phases: list[str] = []
    count_phases: list[str] = []

    class FakeLLM:
        def count_prompt_tokens(self, *, system_prompt, user_prompt, log_context=None):
            phase = (log_context or {}).get("phase")
            count_phases.append(phase)
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=100,
                context_window=10_000,
                status="VERIFIED",
                deployment_id="dep-1",
                route_id="route-1",
                routing_version=1,
            )

        def complete_json(
            self,
            *,
            system_prompt,
            user_prompt,
            log_context=None,
            max_completion_tokens=None,
        ):
            phase = (log_context or {}).get("phase")
            chat_phases.append(phase)
            assert phase == "projects"
            return {
                "pr": [
                    {
                        "n": "fallback사업",
                        "cust": "고객-1",
                        "s": "2020-01",
                        "e": "2021-12",
                        "resp": "PL 운영",
                        "r": [{"d": "D1", "p": 1, "q": "fallback사업"}],
                    }
                ]
            }

    monkeypatch.setattr(
        svc_mod,
        "fetch_model_capabilities",
        lambda settings: ModelCapabilities(
            model_id="qwen3-14b",
            context_window=10_000,
            supports_thinking=True,
            supports_tools=True,
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
            status="VERIFIED",
        ),
    )

    settings = Settings(
        llm_base_url="https://gateway.example.test",
        llm_api_key="k",
        llm_model="qwen3-14b",
        analysis_llm_enable_thinking=False,
        analysis_token_count_max_attempts=3,
        analysis_project_split_max_depth=3,
        analysis_project_split_max_calls=8,
        analysis_core_output_ratio=0.25,
        analysis_core_split_max_depth=3,
        analysis_core_split_max_calls=16,
        analysis_project_output_ratio=0.35,
        analysis_token_safety_ratio=0.05,
    )
    repo = MagicMock()
    repo.list_active_codes.return_value = [
        SimpleNamespace(
            code="JOB-MGT-PL", code_type="JOB", name="PL",
            is_active=True, parent_code=None,
        ),
        SimpleNamespace(
            code="TECH-LANG-PYTHON", code_type="TECH", name="Python",
            is_active=True, parent_code=None,
        ),
        SimpleNamespace(
            code="EXP-MGT", code_type="EXP", name="사업관리",
            is_active=True, parent_code=None,
        ),
    ]
    repo.list_aliases_for_codes.return_value = []

    service = AnalysisService.__new__(AnalysisService)
    service.repo = repo
    service.settings = settings
    service.llm = FakeLLM()
    service.db = MagicMock()
    service.storage = MagicMock()

    page_text = "경력 본문 fallback사업 고객-1 2020-01 2021-12 PL Python 사업관리"
    docs = (
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_id),
            original_filename="a.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_key="k1",
            preview_storage_key=None,
            document_type_code="DOC-PROFILE",
            document_type_name="프로필",
            version_no=1,
            person_id=__import__("uuid").uuid4(),
            pages=[
                PageSnapshot(
                    page_no=1,
                    extracted_text=page_text,
                    layout_json=None,
                )
            ],
        ),
    )
    blocks = (
        f"[DOCUMENT]\n"
        f"document_id: {doc_id}\n"
        f"filename: aaaaaaaa.pdf\n"
        f"document_type: DOC-PROFILE\n"
        f"content:\n[PAGE 1]\n{page_text * 20}"
    )
    precomputed = _core_candidate()
    result = service._extract_staged_compact_candidate(
        prompt=resolve_profile_prompt(SOURCE_FIRST_V31_PROMPT_VERSION),
        document_blocks=blocks,
        catalog_map={
            "JOB-MGT-PL": ("JOB", True),
            "TECH-LANG-PYTHON": ("TECH", True),
            "EXP-MGT": ("EXP", True),
        },
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page_text},
        documents=docs,
        source_char_count=800,
        base_log={"analysis_run_id": "v31-precomputed"},
        vlm_pages=0,
        precomputed_core=precomputed,
    )

    assert result.projects
    assert result.projects[0].project_name == "fallback사업"
    assert chat_phases
    assert all(phase == "projects" for phase in chat_phases)
    assert "core" not in chat_phases
    assert count_phases
    assert all(phase == "projects" for phase in count_phases)
    assert "core" not in count_phases
    assert result.jobs[0].code == "JOB-MGT-PL"


def test_v31_native_success_skips_project_llm_and_returns_native_specs():
    service = AnalysisService.__new__(AnalysisService)
    service.source_builder = object()
    service.settings = SimpleNamespace()

    core = _core_candidate()
    native_candidate = _core_candidate(
        projects=[
            {
                "project_name": "네이티브사업",
                "customer_name": "고객",
                "start_date": "2020.01",
                "end_date": "2020.12",
            }
        ]
    )
    native_specs = [
        DiffSpec(
            entity_type="PROJECT",
            change_type="NEW",
            candidate_path="projects[0]",
            new_value={"project_name": "네이티브사업"},
        )
    ]
    extract_calls: list[dict] = []

    def fake_extract(**kwargs):
        extract_calls.append(kwargs)
        assert kwargs.get("skip_projects") is True
        assert kwargs.get("precomputed_core") is None
        return core

    with patch(
        "app.modules.analysis.source_first_v30_runtime.collect_native_source_rows",
        return_value=[{"row": 1}],
    ), patch(
        "app.modules.analysis.source_first_v30_runtime.assemble_native_from_rows",
        return_value=SimpleNamespace(
            candidate=native_candidate,
            specs=native_specs,
            diagnostics={
                "native_pdf_input_rows": 1,
                "candidate_project_count": 1,
                "native_group_root_quotes_reverified_count": 0,
                "literal_quote_ref_count": 0,
            },
        ),
    ), patch.object(
        AnalysisService,
        "_extract_staged_compact_candidate",
        side_effect=fake_extract,
    ), patch(
        "app.modules.analysis.service.apply_candidate_quality_guards",
        side_effect=lambda c, **kwargs: c,
    ):
        candidate, specs, fallback = service._run_source_first_v31_native_first(
            prompt=SimpleNamespace(guard_core_section_authority=False),
            claimed=SimpleNamespace(documents=[object()]),
            blocks="blocks",
            catalog_map={},
            prompt_source=SimpleNamespace(
                allowed_documents={},
                page_texts={},
            ),
            source_char_count=1000,
            base_log={"analysis_run_id": "v31-ok"},
            vlm_pages=0,
            base_snap={"projects": []},
            confirmed_name="시험사용자",
            document_type_by_id={},
        )

    assert fallback is None
    assert candidate is native_candidate
    assert specs is native_specs
    assert len(extract_calls) == 1
    assert extract_calls[0]["skip_projects"] is True


def test_v31_probe_failure_runs_complete_v25_with_projects():
    service = AnalysisService.__new__(AnalysisService)
    service.source_builder = object()

    full = _core_candidate(
        projects=[
            {
                "project_name": "v25사업",
                "customer_name": "고객",
                "start_date": "2019.01",
                "end_date": "2019.12",
            }
        ]
    )
    extract_calls: list[dict] = []

    def fake_extract(**kwargs):
        extract_calls.append(kwargs)
        assert not kwargs.get("skip_projects")
        assert kwargs.get("precomputed_core") is None
        return full

    with patch(
        "app.modules.analysis.source_first_v30_runtime.collect_native_source_rows",
        side_effect=ValueError("V30_SOURCE_PDF_UNAVAILABLE"),
    ), patch.object(
        AnalysisService,
        "_extract_staged_compact_candidate",
        side_effect=fake_extract,
    ), patch(
        "app.modules.analysis.service.apply_candidate_quality_guards",
        side_effect=lambda c, **kwargs: c,
    ), patch(
        "app.modules.analysis.service.build_diffs",
        return_value=[SimpleNamespace(entity_type="PROJECT")],
    ) as build:
        candidate, specs, fallback = service._run_source_first_v31_native_first(
            prompt=SimpleNamespace(guard_core_section_authority=False),
            claimed=SimpleNamespace(documents=[object()]),
            blocks="blocks",
            catalog_map={},
            prompt_source=SimpleNamespace(
                allowed_documents={},
                page_texts={},
            ),
            source_char_count=1000,
            base_log={"analysis_run_id": "v31-probe"},
            vlm_pages=0,
            base_snap={"projects": []},
            confirmed_name="시험사용자",
            document_type_by_id={},
        )

    assert fallback == "ValueError"
    assert candidate.projects and candidate.projects[0].project_name == "v25사업"
    assert specs
    assert len(extract_calls) == 1
    build.assert_called_once()


def test_v31_assembly_failure_reuses_core_then_runs_project_llm():
    service = AnalysisService.__new__(AnalysisService)
    service.source_builder = object()

    core = _core_candidate()
    full = _core_candidate(
        projects=[
            {
                "project_name": "fallback사업",
                "customer_name": "고객",
                "start_date": "2018.01",
                "end_date": "2018.06",
            }
        ]
    )
    extract_calls: list[dict] = []

    def fake_extract(**kwargs):
        extract_calls.append(kwargs)
        if kwargs.get("skip_projects"):
            return core
        assert kwargs.get("precomputed_core") is not None
        assert kwargs["precomputed_core"].projects == []
        return full

    with patch(
        "app.modules.analysis.source_first_v30_runtime.collect_native_source_rows",
        return_value=[{"row": 1}],
    ), patch(
        "app.modules.analysis.source_first_v30_runtime.assemble_native_from_rows",
        side_effect=ValueError("NATIVE_V30_UNVERIFIED_OR_AMBIGUOUS_SOURCE_ROWS"),
    ), patch.object(
        AnalysisService,
        "_extract_staged_compact_candidate",
        side_effect=fake_extract,
    ), patch(
        "app.modules.analysis.service.apply_candidate_quality_guards",
        side_effect=lambda c, **kwargs: c,
    ), patch(
        "app.modules.analysis.service.build_diffs",
        return_value=[SimpleNamespace(entity_type="PROJECT")],
    ):
        candidate, specs, fallback = service._run_source_first_v31_native_first(
            prompt=SimpleNamespace(guard_core_section_authority=False),
            claimed=SimpleNamespace(documents=[object()]),
            blocks="blocks",
            catalog_map={},
            prompt_source=SimpleNamespace(
                allowed_documents={},
                page_texts={},
            ),
            source_char_count=1000,
            base_log={"analysis_run_id": "v31-asm"},
            vlm_pages=0,
            base_snap={"projects": []},
            confirmed_name="시험사용자",
            document_type_by_id={},
        )

    assert fallback == "ValueError"
    assert candidate.projects and len(candidate.projects) == 1
    assert [c.get("skip_projects") for c in extract_calls] == [True, None]
    assert extract_calls[1]["precomputed_core"] is not None


def test_v31_fallback_rejects_empty_projects():
    service = AnalysisService.__new__(AnalysisService)
    service.source_builder = object()

    with patch(
        "app.modules.analysis.source_first_v30_runtime.collect_native_source_rows",
        side_effect=ValueError("V30_SOURCE_DOCUMENT_WITHOUT_NATIVE_ROWS"),
    ), patch.object(
        AnalysisService,
        "_extract_staged_compact_candidate",
        return_value=_core_candidate(projects=[]),
    ), patch(
        "app.modules.analysis.service.apply_candidate_quality_guards",
        side_effect=lambda c, **kwargs: c,
    ):
        with pytest.raises(InsufficientCandidateError):
            service._run_source_first_v31_native_first(
                prompt=SimpleNamespace(guard_core_section_authority=False),
                claimed=SimpleNamespace(documents=[object()]),
                blocks="blocks",
                catalog_map={},
                prompt_source=SimpleNamespace(
                    allowed_documents={},
                    page_texts={},
                ),
                source_char_count=1000,
                base_log={"analysis_run_id": "v31-empty"},
                vlm_pages=0,
                base_snap={"projects": []},
                confirmed_name="시험사용자",
                document_type_by_id={},
            )


def test_v31_failure_persists_v25_prompt_version():
    events = []
    run = SimpleNamespace(
        status="PROCESSING",
        prompt_version=SOURCE_FIRST_V31_PROMPT_VERSION,
    )

    class FakeRepo:
        def get_run(self, run_id, *, for_update):
            return run

        def replace_diffs(self, run_id, rows):
            events.append(("diffs", rows))

        def mark_reviewing(self, row, *, candidate_json, overall_confidence):
            events.append(("candidate", candidate_json))
            row.status = "REVIEWING"

        def add_audit(self, **kwargs):
            events.append(("audit", kwargs["after"]))

    service = AnalysisService.__new__(AnalysisService)
    service.repo = FakeRepo()
    service.db = SimpleNamespace(commit=lambda: events.append(("commit", None)))
    candidate = SimpleNamespace(
        analysis=SimpleNamespace(overall_confidence=None),
        to_storage_dict=lambda: {"projects": [{"project_name": "v25"}]},
    )
    diff = SimpleNamespace(to_persist_dict=lambda: {"change_type": "NEW"})

    assert (
        service._persist_reviewing(
            uuid4(),
            candidate=candidate,
            specs=[diff],
            actor_user_id=None,
            source_first_fallback="ValueError",
        )
        == "REVIEWING"
    )
    assert run.prompt_version == "profile-extract-v25"
    assert events[2][1]["source_first_v31_fallback_class"] == "ValueError"
    assert events[2][1]["source_first_v30_fallback_class"] is None
