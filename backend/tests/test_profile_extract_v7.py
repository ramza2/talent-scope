"""profile-extract-v7 staged extraction + skill temporal provenance."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
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


def test_profile_extract_registry_v7_remains_staged_historical() -> None:
    """v7 stays resolvable as historical staged; current may advance past v7."""
    from app.ai.prompts import profile_extract_v6 as v6
    from app.ai.prompts import profile_extract_v7 as v7
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    for ver in (
        "profile-extract-v1",
        "profile-extract-v2",
        "profile-extract-v3",
        "profile-extract-v4",
        "profile-extract-v5",
        "profile-extract-v6",
    ):
        spec = resolve_profile_prompt(ver)
        assert spec.prompt_version == ver
        assert spec.extraction_mode == "single"
    assert resolve_profile_prompt("profile-extract-v6").system_prompt == v6.SYSTEM_PROMPT
    v7_spec = resolve_profile_prompt("profile-extract-v7")
    assert v7_spec.extraction_mode == "staged"
    assert v7_spec.compact_protocol is False
    assert v7_spec.core_system_prompt == v7.CORE_SYSTEM_PROMPT
    assert v7_spec.projects_system_prompt == v7.PROJECTS_SYSTEM_PROMPT
    assert "EVERY explicitly named" in v7.CORE_SYSTEM_PROMPT
    assert "ALL distinct documented projects" in v7.PROJECTS_SYSTEM_PROMPT


def test_v6_prompt_file_unchanged_single_mode() -> None:
    from pathlib import Path

    from app.ai.prompts import profile_extract_v6 as v6
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    assert resolve_profile_prompt("profile-extract-v6").extraction_mode == "single"
    assert v6.PROMPT_VERSION == "profile-extract-v6"
    source = Path(v6.__file__).read_text(encoding="utf-8")
    assert "EXTRACTION_MODE" not in source
    assert "staged" not in source


def test_skill_temporal_provenance_clears_without_period_evidence() -> None:
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "jobs": [],
        "skills": [
            {
                "raw_value": "AD",
                "code": "TECH-SEC-AD",
                "last_used_year": 2015,
                "experience_months": 32,
                "source_refs": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "AD / SEP / NAC",
                    }
                ],
            }
        ],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(
        raw,
        catalog={"TECH-SEC-AD": ("TECH", True)},
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): "AD / SEP / NAC 운영"},
    )
    assert doc.skills[0].code == "TECH-SEC-AD"
    assert doc.skills[0].last_used_year is None
    assert doc.skills[0].experience_months is None


def test_skill_temporal_provenance_retains_with_period_evidence() -> None:
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    page = "AD 운영 2012.07~2015.03 (2년 8개월) SEP NAC"
    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "jobs": [],
        "skills": [
            {
                "raw_value": "AD",
                "code": "TECH-SEC-AD",
                "last_used_year": 2015,
                "experience_months": 32,
                "source_refs": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "AD 운영 2012.07~2015.03 (2년 8개월)",
                    }
                ],
            }
        ],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(
        raw,
        catalog={"TECH-SEC-AD": ("TECH", True)},
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page},
    )
    assert doc.skills[0].last_used_year == 2015
    assert doc.skills[0].experience_months == 32


def test_normalize_does_not_calculate_skill_duration() -> None:
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {},
        "skills": [
            {
                "raw_value": "AD",
                "code": "TECH-SEC-AD",
                # No temporal fields supplied — normalize must not invent them.
                "source_refs": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "AD 2012.07~2015.03",
                    }
                ],
            }
        ],
        "jobs": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(
        raw,
        catalog={"TECH-SEC-AD": ("TECH", True)},
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): "AD 2012.07~2015.03"},
    )
    assert doc.skills[0].last_used_year is None
    assert doc.skills[0].experience_months is None


def test_explicit_tech_list_and_coexisting_exp_normalize() -> None:
    from app.modules.analysis.normalize import normalize_candidate

    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "강상원"},
        "jobs": [],
        "skills": [
            {"raw_value": "AD", "code": "TECH-SEC-AD"},
            {"raw_value": "SEP", "code": "TECH-SEC-SEP"},
            {"raw_value": "NAC", "code": "TECH-SEC-NAC"},
        ],
        "expertise": [
            {"raw_value": "정보보안 운영", "code": "EXP-SEC-OPS"},
            {"raw_value": "시스템 운영", "code": "EXP-INFRA"},
            {"raw_value": "사업관리", "code": "EXP-MGT"},
        ],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    catalog = {
        "TECH-SEC-AD": ("TECH", True),
        "TECH-SEC-SEP": ("TECH", True),
        "TECH-SEC-NAC": ("TECH", True),
        "EXP-SEC-OPS": ("EXP", True),
        "EXP-INFRA": ("EXP", True),
        "EXP-MGT": ("EXP", True),
    }
    doc = normalize_candidate(raw, catalog=catalog, allowed_documents={})
    assert [s.code for s in doc.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [e.code for e in doc.expertise] == [
        "EXP-SEC-OPS",
        "EXP-INFRA",
        "EXP-MGT",
    ]


class _StagedSequenceLLM:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.phases: list[str | None] = []
        self.user_prompts: list[str] = []
        self.system_prompts: list[str] = []

    def complete_json(self, **kwargs):
        self.calls += 1
        ctx = kwargs.get("log_context") or {}
        self.phases.append(ctx.get("phase"))
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


def _core_payload(**overrides: Any) -> dict:
    from tests.test_analysis import _valid_candidate_json

    payload = _valid_candidate_json()
    payload["projects"] = []
    payload["skills"] = [
        {"raw_value": "AD", "code": "TECH-SEC-AD"},
        {"raw_value": "SEP", "code": "TECH-SEC-SEP"},
        {"raw_value": "NAC", "code": "TECH-SEC-NAC"},
    ]
    payload["expertise"] = [
        {"raw_value": "정보보안 운영", "code": "EXP-SEC-OPS"},
        {"raw_value": "시스템 운영", "code": "EXP-INFRA"},
        {"raw_value": "사업관리", "code": "EXP-MGT"},
    ]
    payload.update(overrides)
    return payload


def _projects_payload(count: int = 7) -> dict:
    return {
        "projects": [
            {
                "project_name": f"프로젝트-{i}",
                "customer_name": f"고객-{i}",
                "start_date": f"201{i % 10}-01",
                "end_date": f"201{i % 10}-12",
            }
            for i in range(1, count + 1)
        ]
    }


def test_staged_happy_path_two_calls_merge_reviewing(db_session, monkeypatch):
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
        db_session, login_id=f"v7_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "Active Directory")
    _ensure_analysis_code(
        db_session, "TECH-SEC-SEP", "TECH", "Symantec Endpoint Protection"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-NAC", "TECH", "NAC")
    _ensure_analysis_code(db_session, "EXP-SEC-OPS", "EXP", "정보보안 운영")
    _ensure_analysis_code(db_session, "EXP-INFRA", "EXP", "Infra")
    _ensure_analysis_code(db_session, "EXP-MGT", "EXP", "Management")
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
        prompt_version="profile-extract-v7",
    )
    assert run.prompt_version == "profile-extract-v7"

    llm = _StagedSequenceLLM([_core_payload(), _projects_payload(7)])
    build_calls = {"n": 0}
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    real_build = service.source_builder.build

    def _counting_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(service.source_builder, "build", _counting_build)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert build_calls["n"] == 1
    db_session.refresh(run)
    assert run.status == "REVIEWING"
    cand = run.candidate_json
    assert cand is not None
    assert [s["code"] for s in cand["skills"]] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert {e["code"] for e in cand["expertise"]} >= {
        "EXP-SEC-OPS",
        "EXP-INFRA",
        "EXP-MGT",
    }
    assert len(cand["projects"]) == 7
    _cleanup_person(db_session, person.id, admin.id)


def test_staged_projects_completeness_seven_rows(db_session, monkeypatch):
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
        db_session, login_id=f"v7p_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
    llm = _StagedSequenceLLM([_core_payload(), _projects_payload(7)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    db_session.refresh(run)
    names = [p["project_name"] for p in run.candidate_json["projects"]]
    assert names == [f"프로젝트-{i}" for i in range(1, 8)]
    _cleanup_person(db_session, person.id, admin.id)


def test_staged_recovery_budget_max_three_calls(db_session):
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
        db_session, login_id=f"v7r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
    # core truncates once → recovery → projects success => 3 calls
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _core_payload(),
            _projects_payload(3),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    assert "[RECOVERY]" in llm.user_prompts[1]
    assert "[RECOVERY]" not in llm.user_prompts[2]
    _cleanup_person(db_session, person.id, admin.id)


def test_legacy_v6_run_remains_single_call(db_session):
    from app.ai.providers.llm import FakeLLMProvider
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
        db_session, login_id=f"v6l_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
        prompt_version="profile-extract-v6",
    )
    llm = FakeLLMProvider(profile_json=_valid_candidate_json())
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_staged_projects_truncation_recovers_without_rerunning_core(db_session):
    """A: core ok → projects truncates → projects recovery ok (3 calls)."""
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
        db_session, login_id=f"v7pt_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
    llm = _StagedSequenceLLM(
        [
            _core_payload(),
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _projects_payload(4),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    assert "[RECOVERY]" not in llm.user_prompts[0]
    assert "[RECOVERY]" in llm.user_prompts[2]
    db_session.refresh(run)
    assert run.status == "REVIEWING"
    assert len(run.candidate_json["projects"]) == 4
    _cleanup_person(db_session, person.id, admin.id)


def test_staged_projects_truncation_fails_when_recovery_budget_exhausted(db_session):
    """B: core truncates+recovers → projects truncates → FAILED (no projects=[])."""
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
        db_session, login_id=f"v7pe_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _core_payload(),
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "FAILED"
    assert llm.calls <= 3
    assert llm.phases == ["core", "core", "projects"]
    db_session.refresh(run)
    assert run.status == "FAILED"
    # Must not persist a successful empty-project candidate.
    assert run.candidate_json is None or run.candidate_json == {}
    _cleanup_person(db_session, person.id, admin.id)


def test_staged_projects_double_truncation_fails_closed(db_session):
    """C: core ok → projects truncates → projects recovery truncates → FAILED."""
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
        db_session, login_id=f"v7pd_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
    trunc = AIResponseTruncatedError(
        meta={"finish_reason": "length", "total_tokens": 8192}
    )
    llm = _StagedSequenceLLM([_core_payload(), trunc, trunc])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "FAILED"
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert run.candidate_json is None or run.candidate_json == {}
    _cleanup_person(db_session, person.id, admin.id)
