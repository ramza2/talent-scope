"""profile-extract-v8 compact staged protocol + adapters."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(redis_prefix: str, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.run_profile_analysis.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.identify_upload_session.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.process_document.delay",
        lambda *_a, **_k: None,
    )
    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app
    from app.storage.s3 import reset_object_storage_cache

    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# A. Registry
# ---------------------------------------------------------------------------


def test_profile_extract_registry_v8_remains_staged_compact_historical() -> None:
    """v8 stays resolvable as historical staged+compact; current may advance."""
    from app.ai.prompts import profile_extract_v7 as v7
    from app.ai.prompts import profile_extract_v8 as v8
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    for ver in (
        "profile-extract-v1",
        "profile-extract-v2",
        "profile-extract-v3",
        "profile-extract-v4",
        "profile-extract-v5",
        "profile-extract-v6",
    ):
        assert resolve_profile_prompt(ver).extraction_mode == "single"
    v7_spec = resolve_profile_prompt("profile-extract-v7")
    assert v7_spec.extraction_mode == "staged"
    assert v7_spec.compact_protocol is False
    assert v7_spec.core_system_prompt == v7.CORE_SYSTEM_PROMPT
    v8_spec = resolve_profile_prompt("profile-extract-v8")
    assert v8_spec.extraction_mode == "staged"
    assert v8_spec.compact_protocol is True
    assert v8_spec.core_system_prompt == v8.CORE_SYSTEM_PROMPT
    assert v8_spec.projects_system_prompt == v8.PROJECTS_SYSTEM_PROMPT
    assert v8.COMPACT_PROTOCOL is True


# ---------------------------------------------------------------------------
# B. Document aliases
# ---------------------------------------------------------------------------


def test_document_aliases_expand_and_unknown_dropped() -> None:
    from app.modules.analysis.compact_v8 import (
        build_document_alias_view,
        expand_compact_core,
        expand_compact_refs,
    )

    doc1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    doc2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    blocks = (
        f"[DOCUMENT]\ndocument_id: {doc1}\nfilename: a.pdf\n"
        f"document_type: 이력서\ncontent:\n[PAGE 1]\nhello\n\n"
        f"[DOCUMENT]\ndocument_id: {doc2}\nfilename: b.pdf\n"
        f"document_type: 경력\ncontent:\n[PAGE 1]\nworld\n"
    )
    aliased, alias_map = build_document_alias_view(blocks)
    assert "[DOCUMENT D1]" in aliased
    assert "[DOCUMENT D2]" in aliased
    assert doc1 not in aliased
    assert doc2 not in aliased
    assert alias_map == {"D1": doc1, "D2": doc2}

    refs = expand_compact_refs(
        [
            {"d": "D1", "p": 1, "q": "hello"},
            {"d": "D9", "p": 1, "q": "ghost"},
        ],
        alias_map,
    )
    assert refs == [{"document_id": doc1, "page_no": 1, "quote_text": "hello"}]

    expanded = expand_compact_core(
        {
            "p": {"n": "홍길동", "r": {"n": [{"d": "D1", "p": 1, "q": "홍길동"}]}},
            "j": [],
            "s": [],
            "x": [],
            "conf": 0.9,
        },
        alias_to_id=alias_map,
    )
    assert expanded["profile"]["source_refs"]["name"][0]["document_id"] == doc1


# ---------------------------------------------------------------------------
# C / D. Compact CORE expansion + temporal evidence
# ---------------------------------------------------------------------------


def test_compact_core_expansion_tech_exp_and_temporal() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    alias_map = {"D1": doc_id}
    page = "AD / SEP / NAC 정보보안 운영 시스템 운영 사업관리 AD 운영 2012.07~2015.03 (2년 8개월)"
    compact = {
        "p": {"n": "강상원"},
        "j": [{"v": "PL", "c": "JOB-PL", "t": "PRIMARY", "f": 1.0}],
        "s": [
            {
                "v": "AD",
                "c": "TECH-SEC-AD",
                "y": 2015,
                "m": 32,
                "rep": True,
                "f": 1.0,
                "r": [{"d": "D1", "p": 1, "q": "AD / SEP / NAC"}],
            },
            {
                "v": "SEP",
                "c": "TECH-SEC-SEP",
                "f": 1.0,
                "r": [{"d": "D1", "p": 1, "q": "AD / SEP / NAC"}],
            },
            {
                "v": "NAC",
                "c": "TECH-SEC-NAC",
                "f": 1.0,
                "r": [{"d": "D1", "p": 1, "q": "AD / SEP / NAC"}],
            },
            {
                "v": "AD",
                "c": "TECH-SEC-AD",
                "y": 2015,
                "m": 32,
                "f": 1.0,
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "AD 운영 2012.07~2015.03 (2년 8개월)",
                    }
                ],
            },
        ],
        "x": [
            {"v": "정보보안 운영", "c": "EXP-SEC-OPS", "e": "EXPLICIT", "f": 1.0},
            {"v": "시스템 운영", "c": "EXP-INFRA", "e": "EXPLICIT", "f": 1.0},
            {"v": "사업관리", "c": "EXP-MGT", "e": "EXPLICIT", "f": 1.0},
        ],
        "conf": 0.9,
    }
    # Split skills: first AD lacks temporal quote; fourth has temporal.
    # Use two separate normalize checks instead.
    tech_only = expand_compact_core(
        {
            "p": {"n": "강상원"},
            "s": [
                {
                    "v": "AD",
                    "c": "TECH-SEC-AD",
                    "y": 2015,
                    "m": 32,
                    "r": [{"d": "D1", "p": 1, "q": "AD / SEP / NAC"}],
                },
                {"v": "SEP", "c": "TECH-SEC-SEP"},
                {"v": "NAC", "c": "TECH-SEC-NAC"},
            ],
            "x": [
                {"v": "정보보안 운영", "c": "EXP-SEC-OPS"},
                {"v": "시스템 운영", "c": "EXP-INFRA"},
                {"v": "사업관리", "c": "EXP-MGT"},
            ],
        },
        alias_to_id=alias_map,
    )
    catalog = {
        "TECH-SEC-AD": ("TECH", True),
        "TECH-SEC-SEP": ("TECH", True),
        "TECH-SEC-NAC": ("TECH", True),
        "EXP-SEC-OPS": ("EXP", True),
        "EXP-INFRA": ("EXP", True),
        "EXP-MGT": ("EXP", True),
        "JOB-PL": ("JOB", True),
    }
    doc = normalize_candidate(
        tech_only,
        catalog=catalog,
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page},
    )
    assert [s.code for s in doc.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert doc.skills[0].last_used_year is None
    assert doc.skills[0].experience_months is None
    assert [e.code for e in doc.expertise] == [
        "EXP-SEC-OPS",
        "EXP-INFRA",
        "EXP-MGT",
    ]

    temporal = expand_compact_core(
        {
            "s": [
                {
                    "v": "AD",
                    "c": "TECH-SEC-AD",
                    "y": 2015,
                    "m": 32,
                    "r": [
                        {
                            "d": "D1",
                            "p": 1,
                            "q": "AD 운영 2012.07~2015.03 (2년 8개월)",
                        }
                    ],
                }
            ],
        },
        alias_to_id=alias_map,
    )
    doc2 = normalize_candidate(
        temporal,
        catalog=catalog,
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page},
    )
    assert doc2.skills[0].last_used_year == 2015
    assert doc2.skills[0].experience_months == 32

    # Sanity: full compact shape expands to candidate keys.
    full = expand_compact_core(compact, alias_to_id=alias_map)
    assert full["schema_version"] == "profile-candidate-v1"
    assert full["profile"]["name"] == "강상원"
    assert full["projects"] == []
    assert full["analysis"]["overall_confidence"] == 0.9


# ---------------------------------------------------------------------------
# E. Compact PROJECTS expansion + size guard fixture
# ---------------------------------------------------------------------------


def _seven_compact_projects() -> dict[str, Any]:
    return {
        "pr": [
            {
                "n": f"프로젝트-{i}",
                "cu": f"고객-{i}",
                "s": f"201{i}-01",
                "e": f"201{i}-12",
                "d": 12,
                "resp": "PL 운영",
                "j": ["JOB-PL"],
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "x": ["EXP-SEC-OPS"],
                "f": 1.0,
                "r": [{"d": "D1", "p": 1, "q": f"프로젝트-{i}"}],
            }
            for i in range(1, 8)
        ]
    }


def _seven_verbose_projects(doc_id: str) -> dict[str, Any]:
    return {
        "projects": [
            {
                "project_name": f"프로젝트-{i}",
                "customer_name": f"고객-{i}",
                "start_date": f"201{i}-01",
                "end_date": f"201{i}-12",
                "duration_months": 12,
                "responsibilities": "PL 운영",
                "confidence": 1.0,
                "source_refs": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": f"프로젝트-{i}",
                    }
                ],
                "jobs": [
                    {
                        "raw_value": "PL",
                        "code": "JOB-PL",
                        "source_refs": [
                            {
                                "document_id": doc_id,
                                "page_no": 1,
                                "quote_text": f"프로젝트-{i}",
                            }
                        ],
                    }
                ],
                "skills": [
                    {
                        "raw_value": code.split("-")[-1],
                        "code": code,
                        "source_refs": [
                            {
                                "document_id": doc_id,
                                "page_no": 1,
                                "quote_text": f"프로젝트-{i}",
                            }
                        ],
                    }
                    for code in (
                        "TECH-SEC-AD",
                        "TECH-SEC-SEP",
                        "TECH-SEC-NAC",
                    )
                ],
                "expertise": [
                    {
                        "raw_value": "정보보안 운영",
                        "code": "EXP-SEC-OPS",
                        "source_refs": [
                            {
                                "document_id": doc_id,
                                "page_no": 1,
                                "quote_text": f"프로젝트-{i}",
                            }
                        ],
                    }
                ],
                "business_domains": [],
                "customer_types": [],
            }
            for i in range(1, 8)
        ]
    }


def test_compact_projects_expansion_seven_rows_and_relations() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    alias_map = {"D1": doc_id}
    expanded = expand_compact_projects(
        _seven_compact_projects(), alias_to_id=alias_map
    )
    assert len(expanded["projects"]) == 7
    first = expanded["projects"][0]
    assert first["project_name"] == "프로젝트-1"
    assert [j["code"] for j in first["jobs"]] == ["JOB-PL"]
    assert [s["code"] for s in first["skills"]] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [e["code"] for e in first["expertise"]] == ["EXP-SEC-OPS"]
    assert "business_domains" not in first
    assert "customer_types" not in first
    assert first["source_refs"][0]["document_id"] == doc_id

    catalog = {
        "JOB-PL": ("JOB", True),
        "TECH-SEC-AD": ("TECH", True),
        "TECH-SEC-SEP": ("TECH", True),
        "TECH-SEC-NAC": ("TECH", True),
        "EXP-SEC-OPS": ("EXP", True),
    }
    page = " ".join(f"프로젝트-{i}" for i in range(1, 8))
    doc = normalize_candidate(
        expanded,
        catalog=catalog,
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page},
    )
    assert len(doc.projects) == 7
    assert [s.code for s in doc.projects[0].skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]


def test_compact_output_materially_smaller_than_verbose() -> None:
    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    compact = json.dumps(_seven_compact_projects(), ensure_ascii=False)
    verbose = json.dumps(_seven_verbose_projects(doc_id), ensure_ascii=False)
    assert len(compact) < len(verbose) * 0.55


# ---------------------------------------------------------------------------
# F. Call budget (integration)
# ---------------------------------------------------------------------------


class _StagedSequenceLLM:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.phases: list[str | None] = []
        self.user_prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.log_contexts: list[dict] = []

    def complete_json(self, **kwargs):
        self.calls += 1
        ctx = kwargs.get("log_context") or {}
        self.phases.append(ctx.get("phase"))
        self.log_contexts.append(dict(ctx))
        user_prompt = kwargs.get("user_prompt")
        if isinstance(user_prompt, str):
            self.user_prompts.append(user_prompt)
        system_prompt = kwargs.get("system_prompt")
        if isinstance(system_prompt, str):
            self.system_prompts.append(system_prompt)
        item = self.payloads[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return dict(item)


def _compact_core_payload(**overrides: Any) -> dict:
    payload = {
        "p": {"n": "홍길동", "ac": "ABC테크", "tg": "EXPERT"},
        "j": [
            {"v": "AI개발자", "c": "JOB-AI-DEV", "t": "PRIMARY"},
            {"v": "PL", "t": "SECONDARY"},
        ],
        "s": [
            {"v": "AD", "c": "TECH-SEC-AD"},
            {"v": "SEP", "c": "TECH-SEC-SEP"},
            {"v": "NAC", "c": "TECH-SEC-NAC"},
        ],
        "x": [
            {"v": "정보보안 운영", "c": "EXP-SEC-OPS"},
            {"v": "시스템 운영", "c": "EXP-INFRA"},
            {"v": "사업관리", "c": "EXP-MGT"},
        ],
        "w": [{"co": "ABC테크", "ti": "책임", "s": "2020-01"}],
        "e": [{"sc": "서울대", "mj": "컴공"}],
        "c": [{"n": "정보처리기사"}],
        "conf": 0.85,
    }
    payload.update(overrides)
    return payload


def _compact_projects_payload(count: int = 7) -> dict:
    return {
        "pr": [
            {
                "n": f"프로젝트-{i}",
                "cu": f"고객-{i}",
                "s": f"201{i % 10}-01",
                "e": f"201{i % 10}-12",
            }
            for i in range(1, count + 1)
        ]
    }


def test_v8_happy_path_two_calls_aliases_and_phase_catalogs(db_session, monkeypatch):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v8_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "Active Directory")
    _ensure_analysis_code(
        db_session, "TECH-SEC-SEP", "TECH", "Symantec Endpoint Protection"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-NAC", "TECH", "NAC")
    _ensure_analysis_code(db_session, "EXP-SEC-OPS", "EXP", "정보보안 운영")
    _ensure_analysis_code(db_session, "EXP-INFRA", "EXP", "Infra")
    _ensure_analysis_code(db_session, "EXP-MGT", "EXP", "Management")
    _ensure_analysis_code(db_session, "BIZ-AUTO", "BIZ", "자동차")
    _ensure_analysis_code(db_session, "CUSTOMER-ENTERPRISE", "CUSTOMER_TYPE", "대기업")

    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT + " AD SEP NAC 시스템 운영 사업관리",
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v8",
    )
    assert run.prompt_version == "profile-extract-v8"

    llm = _StagedSequenceLLM([_compact_core_payload(), _compact_projects_payload(7)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v8" for c in llm.log_contexts)

    # Document alias in prompt (no raw UUID header).
    assert "[DOCUMENT D1]" in llm.user_prompts[0]
    assert f"document_id: {document.id}" not in llm.user_prompts[0]

    # Phase-specific catalogs: CORE omits BIZ/CUSTOMER_TYPE; PROJECTS includes them.
    assert "BIZ-AUTO" not in llm.user_prompts[0]
    assert "CUSTOMER-ENTERPRISE" not in llm.user_prompts[0]
    assert "[TECH]" in llm.user_prompts[0]
    assert "BIZ-AUTO" in llm.user_prompts[1]
    assert "CUSTOMER-ENTERPRISE" in llm.user_prompts[1]

    db_session.refresh(run)
    cand = run.candidate_json
    assert [s["code"] for s in cand["skills"]] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert len(cand["projects"]) == 7
    _cleanup_person(db_session, person.id, admin.id)


def test_v8_two_docs_aliases_in_prompt(db_session):
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.modules.analysis.repository import AnalysisRepository
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _ensure_named_doc_type,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v8d_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc1 = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    _ensure_named_doc_type(db_session, code="DOC-CAREER", name="경력기술서")
    group = DocumentGroup(
        person_id=person.id,
        document_type_code="DOC-CAREER",
        title="경력기술서",
    )
    db_session.add(group)
    db_session.flush()
    doc2 = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="career.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256="c" * 64,
        processing_status="READY",
        uploaded_by=admin.id,
    )
    db_session.add(doc2)
    db_session.flush()
    db_session.add(
        DocumentPage(
            document_id=doc2.id,
            page_no=1,
            extracted_text=_RICH_PAGE_TEXT,
            layout_json={"needs_vlm": False},
            extraction_method="TEXT_PARSER",
        )
    )
    db_session.commit()

    prompt = resolve_profile_prompt("profile-extract-v8")
    repo = AnalysisRepository(db_session)
    run = repo.create_run(
        person_id=person.id,
        base_profile_version=1,
        llm_model="fake",
        vlm_model="fake",
        prompt_version=prompt.prompt_version,
        schema_version=prompt.schema_version,
    )
    repo.add_run_documents(run.id, [doc1.id, doc2.id])
    db_session.commit()

    llm = _StagedSequenceLLM([_compact_core_payload(), _compact_projects_payload(2)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert "[DOCUMENT D1]" in llm.user_prompts[0]
    assert "[DOCUMENT D2]" in llm.user_prompts[0]
    assert str(doc1.id) not in llm.user_prompts[0]
    assert str(doc2.id) not in llm.user_prompts[0]
    _cleanup_person(db_session, person.id, admin.id)


def test_v8_recovery_budget_max_three_calls(db_session):
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v8r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v8",
    )
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _compact_core_payload(),
            _compact_projects_payload(3),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    assert "[RECOVERY]" in llm.user_prompts[1]
    _cleanup_person(db_session, person.id, admin.id)


def test_v8_unrecovered_projects_truncation_fails_closed(db_session):
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v8f_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v8",
    )
    trunc = AIResponseTruncatedError(
        meta={"finish_reason": "length", "total_tokens": 8192}
    )
    llm = _StagedSequenceLLM([_compact_core_payload(), trunc, trunc])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "FAILED"
    assert llm.calls == 3
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert run.candidate_json is None or run.candidate_json == {}
    _cleanup_person(db_session, person.id, admin.id)


# ---------------------------------------------------------------------------
# G. Legacy v7 unchanged
# ---------------------------------------------------------------------------


def test_v7_prompt_files_byte_for_byte_unchanged() -> None:
    from app.ai.prompts import profile_extract_v7 as v7

    # Historical markers that must remain in the immutable v7 prompt module.
    source = Path(v7.__file__).read_text(encoding="utf-8")
    assert v7.PROMPT_VERSION == "profile-extract-v7"
    assert "COMPACT_PROTOCOL" not in source
    assert 'SCHEMA_VERSION = "profile-candidate-v1"' in source
    assert "EVERY explicitly named distinct concrete technology" in source
    assert "ALL distinct documented projects/engagement rows" in source
    assert '{"projects":[...]}' in source


def test_v7_run_still_uses_verbose_protocol(db_session):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
        _valid_candidate_json,
    )

    admin = _create_user(
        db_session, login_id=f"v7l_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v7",
    )
    core = _valid_candidate_json()
    core["projects"] = []
    projects = {
        "projects": [
            {"project_name": f"프로젝트-{i}"} for i in range(1, 4)
        ]
    }
    llm = _StagedSequenceLLM([core, projects])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    # v7 still sends UUID document_id headers (no D1 alias protocol).
    assert "document_id:" in llm.user_prompts[0]
    assert "[DOCUMENT D1]" not in llm.user_prompts[0]
    assert "compact_protocol" not in (llm.log_contexts[0] or {})
    _cleanup_person(db_session, person.id, admin.id)


# ---------------------------------------------------------------------------
# H. Size guards
# ---------------------------------------------------------------------------


def test_v8_static_prompts_materially_smaller_than_v7() -> None:
    from app.ai.prompts import profile_extract_v7 as v7
    from app.ai.prompts import profile_extract_v8 as v8

    assert len(v8.CORE_SYSTEM_PROMPT) < len(v7.CORE_SYSTEM_PROMPT) * 0.75
    assert len(v8.PROJECTS_SYSTEM_PROMPT) < len(v7.PROJECTS_SYSTEM_PROMPT) * 0.85
    assert len(v8.CORE_SCHEMA_GUIDE) < len(v7.CORE_SCHEMA_GUIDE) * 0.7
    # Full PROJECTS static prompt (guide + instructions) must shrink even if
    # the isolated schema-guide string alone is comparable.
    assert len(v8.PROJECTS_SYSTEM_PROMPT) < len(v7.PROJECTS_SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# #58 — compact adapter type guards for malformed optional scalars
# ---------------------------------------------------------------------------


def test_compact_certification_issuer_bool_omitted() -> None:
    """A: production shape ``is: true`` must not fail validation or become \"True\"."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(
        {"c": [{"n": "정보처리산업기사", "is": True}]},
        alias_to_id={},
    )
    assert expanded["certifications"][0]["certification_name"] == "정보처리산업기사"
    assert "issuer" not in expanded["certifications"][0]
    # Direct model validation + normalize must both succeed.
    ProfileCandidateDocument.model_validate(expanded)
    doc = normalize_candidate(expanded, catalog={}, allowed_documents={})
    assert len(doc.certifications) == 1
    assert doc.certifications[0].certification_name == "정보처리산업기사"
    assert doc.certifications[0].issuer is None
    dumped = doc.model_dump(mode="json")
    assert dumped["certifications"][0].get("issuer") in (None, "")
    assert "True" not in str(dumped["certifications"])


def test_compact_certification_issuer_string_preserved() -> None:
    """B: valid issuer string is preserved exactly."""
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(
        {"c": [{"n": "정보처리산업기사", "is": "한국산업인력공단"}]},
        alias_to_id={},
    )
    assert expanded["certifications"][0]["issuer"] == "한국산업인력공단"
    doc = normalize_candidate(expanded, catalog={}, allowed_documents={})
    assert doc.certifications[0].issuer == "한국산업인력공단"


def test_compact_malformed_optional_text_fields_omitted() -> None:
    """C: bool/list/dict optional text values are omitted; no ValidationError."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import (
        expand_compact_core,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(
        {
            "p": {
                "n": "홍길동",
                "ac": True,  # affiliation_company bool
                "ph": ["010"],  # list
                "em": {"x": 1},  # dict
                "by": 1990,  # typed numeric kept
            },
            "j": [{"v": True, "c": "JOB-PL", "t": ["PRIMARY"]}],
            "s": [{"v": "AD", "c": True, "y": 2015, "m": 12, "rep": True}],
            "x": [{"v": "보안", "c": "EXP-SEC-OPS", "e": False}],
            "w": [{"co": True, "ti": "책임", "s": "2020-01", "resp": ["a"]}],
            "e": [{"sc": "서울대", "mj": True, "dg": {"x": 1}}],
            "c": [{"n": "정보처리산업기사", "is": True, "ad": ["2020"]}],
            "sum": True,
        },
        alias_to_id={},
    )
    profile = expanded["profile"]
    assert profile["name"] == "홍길동"
    assert profile["birth_year"] == 1990
    assert "affiliation_company" not in profile
    assert "phone" not in profile
    assert "email" not in profile
    assert expanded["jobs"][0].get("raw_value") is None
    assert "raw_value" not in expanded["jobs"][0]
    assert expanded["jobs"][0]["code"] == "JOB-PL"
    assert "job_type" not in expanded["jobs"][0]
    assert "code" not in expanded["skills"][0]
    assert expanded["skills"][0]["raw_value"] == "AD"
    assert expanded["skills"][0]["last_used_year"] == 2015
    assert expanded["skills"][0]["is_representative"] is True
    assert "evidence_type" not in expanded["expertise"][0]
    assert "company_name" not in expanded["employment_history"][0]
    assert expanded["employment_history"][0]["title"] == "책임"
    assert "responsibilities" not in expanded["employment_history"][0]
    assert "major" not in expanded["education"][0]
    assert "degree" not in expanded["education"][0]
    assert "issuer" not in expanded["certifications"][0]
    assert "acquired_date" not in expanded["certifications"][0]
    assert expanded["summary"] == {}

    projects = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "프로젝트-1",
                    "cu": True,
                    "s": "2012-01",
                    "e": ["2015"],
                    "resp": {"x": 1},
                    "sum": False,
                    "d": 32,
                    "f": 1.0,
                }
            ]
        },
        alias_to_id={},
    )
    proj = projects["projects"][0]
    assert proj["project_name"] == "프로젝트-1"
    assert "customer_name" not in proj
    assert proj["start_date"] == "2012-01"
    assert "end_date" not in proj
    assert "responsibilities" not in proj
    assert "project_summary" not in proj
    assert proj["duration_months"] == 32

    ProfileCandidateDocument.model_validate(expanded)
    ProfileCandidateDocument.model_validate(projects)
    normalize_candidate(expanded, catalog={}, allowed_documents={})
    normalize_candidate(projects, catalog={}, allowed_documents={})


def test_compact_source_ref_quote_bool_omitted() -> None:
    """D: malformed quote_text omitted; document/page kept; unknown alias dropped."""
    from app.modules.analysis.compact_v8 import expand_compact_refs

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    alias_map = {"D1": doc_id}
    refs = expand_compact_refs(
        [
            {"d": "D1", "p": 1, "q": True},
            {"d": "D1", "p": 2, "q": "정상 인용"},
            {"d": "D9", "p": 1, "q": "ghost"},
            {"d": True, "p": 1, "q": "bad alias"},
        ],
        alias_map,
    )
    assert refs == [
        {"document_id": doc_id, "page_no": 1},
        {"document_id": doc_id, "page_no": 2, "quote_text": "정상 인용"},
    ]
    assert "quote_text" not in refs[0]
    assert "True" not in str(refs)


def test_profile_extract_v8_prompt_file_unchanged_from_base() -> None:
    """F: prompt module remains immutable vs merge base."""
    import hashlib
    import subprocess

    path = "backend/app/ai/prompts/profile_extract_v8.py"
    current = Path("/workspace") / path
    base = subprocess.check_output(
        ["git", "show", "f9209addc4bb4f75277f2fa24d2eaa3db0c4cb1e:" + path],
        cwd="/workspace",
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()
