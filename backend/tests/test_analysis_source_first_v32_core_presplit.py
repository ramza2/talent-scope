"""V32 CORE length-risk pre-split (reuses V31 native-first; FakeLLM only)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.prompts.profile_extract import (
    SOURCE_FIRST_V30_PROMPT_VERSION,
    SOURCE_FIRST_V31_PROMPT_VERSION,
    SOURCE_FIRST_V32_PROMPT_VERSION,
    current_profile_prompt,
    resolve_profile_prompt,
)
from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.schemas import CreateAnalysisRequest
from app.modules.analysis.service import AnalysisService
from app.modules.analysis.token_budget import plan_core_length_risk_presplit


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


def test_v32_opt_in_default_false_and_exclusive():
    base = {"person_id": uuid4(), "document_ids": [uuid4()]}
    assert CreateAnalysisRequest(**base).source_first_v32 is False
    assert CreateAnalysisRequest(**base, source_first_v32=True).source_first_v32 is True
    for invalid in ("true", 1, None):
        with pytest.raises(ValidationError):
            CreateAnalysisRequest(**base, source_first_v32=invalid)
    with pytest.raises(ValidationError, match="동시에"):
        CreateAnalysisRequest(**base, source_first_v31=True, source_first_v32=True)
    with pytest.raises(ValidationError, match="동시에"):
        CreateAnalysisRequest(**base, source_first_v30=True, source_first_v32=True)


def test_v32_prompt_selection_preserves_v25_v30_v31_defaults():
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
    assert (
        service._selected_profile_prompt(source_first_v32=True).prompt_version
        == SOURCE_FIRST_V32_PROMPT_VERSION
    )
    with pytest.raises(Exception, match="동시에"):
        service._selected_profile_prompt(
            source_first_v31=True, source_first_v32=True
        )
    v25 = current_profile_prompt()
    v32 = resolve_profile_prompt(SOURCE_FIRST_V32_PROMPT_VERSION)
    assert v32.core_system_prompt == v25.core_system_prompt
    assert v32.projects_system_prompt == v25.projects_system_prompt


def test_plan_core_length_risk_presplit_requires_multi_unit_fit():
    calls: list[str] = []

    class Budget:
        def __init__(self, fits: bool):
            self.fits = fits

    def budget_for_blocks(blocks: str):
        calls.append(blocks)
        # Combined and each page fit.
        return Budget(True)

    single = _doc_block(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        pages="[PAGE 1]\n" + ("본문 " * 40),
    )
    assert plan_core_length_risk_presplit(
        single, budget_for_blocks=budget_for_blocks, max_chunks=8
    ) is None

    multi = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            pages="[PAGE 1]\nAAA\n\n[PAGE 2]\n" + ("문서A " * 40),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            pages="[PAGE 1]\n" + ("문서B " * 40),
        )
    )
    planned = plan_core_length_risk_presplit(
        multi, budget_for_blocks=budget_for_blocks, max_chunks=8
    )
    assert planned is not None
    assert len(planned) >= 2
    assert all("DOCUMENT" in chunk or "PAGE" in chunk for chunk in planned)


def test_plan_core_length_risk_presplit_skips_when_leaf_does_not_fit():
    class Budget:
        def __init__(self, fits: bool):
            self.fits = fits

    def budget_for_blocks(blocks: str):
        pages = blocks.count("[PAGE ")
        docs = blocks.count("[DOCUMENT]")
        if docs > 1 or pages > 1:
            return Budget(True)  # root fits
        return Budget(False)  # leaf does not

    multi = _doc_block(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        pages="[PAGE 1]\nA\n\n[PAGE 2]\nB",
    )
    assert plan_core_length_risk_presplit(
        multi, budget_for_blocks=budget_for_blocks, max_chunks=8
    ) is None


def test_v32_presplit_avoids_wasted_single_path_length_chat(monkeypatch):
    """Multi-page fitting source must not Chat the combined root first."""
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.ai.providers.model_capabilities import ModelCapabilities
    from app.ai.providers.token_count import TokenCountResult
    from app.core.config import Settings
    import app.modules.analysis.service as svc_mod
    from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot

    doc_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    doc_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    chat_prompts: list[str] = []

    class FakeLLM:
        def count_prompt_tokens(self, *, system_prompt, user_prompt, log_context=None):
            both = doc_a[:8] in user_prompt and doc_b[:8] in user_prompt
            # Aliased docs use D1/D2; detect combined vs leaf by page markers.
            page_markers = user_prompt.count("[PAGE ")
            doc_markers = user_prompt.count("[DOCUMENT")
            combined = doc_markers > 1 or page_markers > 1
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=1200 if combined else 500,
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
            assert phase == "core"
            chat_prompts.append(user_prompt)
            # Combined root would truncate — V32 must never chat it first.
            if user_prompt.count("[DOCUMENT") > 1 or user_prompt.count("[PAGE ") > 1:
                raise AIResponseTruncatedError(
                    meta={"finish_reason": "length", "prompt_tokens": 1200}
                )
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

    blocks = (
        _doc_block(doc_a, pages="[PAGE 1]\n" + ("문서A 경력 본문 " * 30))
        + _doc_block(doc_b, pages="[PAGE 1]\n" + ("문서B 경력 본문 " * 30))
    )
    docs = (
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_a),
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
                    page_no=1, extracted_text="문서A 경력 본문", layout_json=None
                )
            ],
        ),
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_b),
            original_filename="b.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_key="k2",
            preview_storage_key=None,
            document_type_code="DOC-PROFILE",
            document_type_name="프로필",
            version_no=1,
            person_id=__import__("uuid").uuid4(),
            pages=[
                PageSnapshot(
                    page_no=1, extracted_text="문서B 경력 본문", layout_json=None
                )
            ],
        ),
    )
    result = service._extract_staged_compact_candidate(
        prompt=resolve_profile_prompt(SOURCE_FIRST_V32_PROMPT_VERSION),
        document_blocks=blocks,
        catalog_map={
            "JOB-MGT-PL": ("JOB", True),
            "TECH-LANG-PYTHON": ("TECH", True),
            "EXP-MGT": ("EXP", True),
        },
        allowed_documents={doc_a: {1}, doc_b: {1}},
        page_texts={
            (doc_a, 1): "문서A 경력 본문 PL Python 사업관리",
            (doc_b, 1): "문서B 경력 본문 PL Python 사업관리",
        },
        documents=docs,
        source_char_count=1200,
        base_log={"analysis_run_id": "v32-presplit"},
        vlm_pages=0,
        skip_projects=True,
        core_length_presplit=True,
    )

    assert isinstance(result, ProfileCandidateDocument)
    assert result.projects == []
    assert chat_prompts
    assert all(
        p.count("[DOCUMENT") <= 1 and p.count("[PAGE ") <= 1 for p in chat_prompts
    )
    assert len(chat_prompts) >= 2


def test_v31_without_presplit_flag_still_chats_combined_root(monkeypatch):
    """V31 default path must keep single-path Chat when root fits."""
    from app.ai.providers.model_capabilities import ModelCapabilities
    from app.ai.providers.token_count import TokenCountResult
    from app.core.config import Settings
    import app.modules.analysis.service as svc_mod
    from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot

    doc_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    doc_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    chat_prompts: list[str] = []

    class FakeLLM:
        def count_prompt_tokens(self, *, system_prompt, user_prompt, log_context=None):
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=800,
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
            chat_prompts.append(user_prompt)
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

    blocks = (
        _doc_block(doc_a, pages="[PAGE 1]\n" + ("문서A 경력 본문 " * 30))
        + _doc_block(doc_b, pages="[PAGE 1]\n" + ("문서B 경력 본문 " * 30))
    )
    docs = (
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_a),
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
                    page_no=1, extracted_text="문서A", layout_json=None
                )
            ],
        ),
        DocumentSnapshot(
            id=__import__("uuid").UUID(doc_b),
            original_filename="b.pdf",
            extension="pdf",
            mime_type="application/pdf",
            storage_key="k2",
            preview_storage_key=None,
            document_type_code="DOC-PROFILE",
            document_type_name="프로필",
            version_no=1,
            person_id=__import__("uuid").uuid4(),
            pages=[
                PageSnapshot(
                    page_no=1, extracted_text="문서B", layout_json=None
                )
            ],
        ),
    )
    service._extract_staged_compact_candidate(
        prompt=resolve_profile_prompt(SOURCE_FIRST_V31_PROMPT_VERSION),
        document_blocks=blocks,
        catalog_map={
            "JOB-MGT-PL": ("JOB", True),
            "TECH-LANG-PYTHON": ("TECH", True),
            "EXP-MGT": ("EXP", True),
        },
        allowed_documents={doc_a: {1}, doc_b: {1}},
        page_texts={(doc_a, 1): "문서A", (doc_b, 1): "문서B"},
        documents=docs,
        source_char_count=1200,
        base_log={"analysis_run_id": "v31-no-presplit"},
        vlm_pages=0,
        skip_projects=True,
        core_length_presplit=False,
    )
    assert len(chat_prompts) == 1
    assert chat_prompts[0].count("[DOCUMENT") >= 2


def test_v32_create_analysis_opt_in_is_per_run(monkeypatch):
    person_id, document_id, actor_id = uuid4(), uuid4(), uuid4()
    written, audits = [], []

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
        lambda run_id, user_id: None,
    )
    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person_id,
            document_ids=[document_id],
            source_first_v32=True,
        ),
        actor_id,
    )
    service.create_analysis(
        CreateAnalysisRequest(person_id=person_id, document_ids=[document_id]),
        actor_id,
    )
    assert [r["prompt_version"] for r in written] == [
        SOURCE_FIRST_V32_PROMPT_VERSION,
        "profile-extract-v25",
    ]
    assert [a["after"]["source_first_v32_opt_in"] for a in audits] == [True, False]
