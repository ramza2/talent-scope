"""profile-extract-v16 CORE structured-sparse recovery hardening."""

from __future__ import annotations

import hashlib
import os
import subprocess
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

_BASE_SHA = "54d589f07c43a383fc74f091c41e24a617186eb2"
_REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _sparse_core_px_only() -> dict:
    """Production-like CORE: profile scalars + one expertise; no w/e/c/j/s."""
    return {
        "p": {
            "n": "홍길동",
            "tg": "EXPERT",
            "ac": "ABC테크",
            "dp": "AI개발팀",
            "ti": "책임",
            "cdv": "특급 15년",
        },
        "x": [{"v": "사업관리", "c": "EXP-MGT"}],
        "conf": 0.9,
    }


def _complete_core() -> dict:
    return {
        "p": {"n": "홍길동", "tg": "EXPERT", "ac": "ABC테크"},
        "j": [{"v": "PL", "c": "JOB-MGT-PL", "t": "PRIMARY"}],
        "s": [{"v": "AD", "c": "TECH-SEC-AD"}],
        "x": [{"v": "사업관리", "c": "EXP-MGT"}],
        "w": [
            {"co": "ABC테크", "ti": "책임", "s": "2018-01", "e": "2024-12"},
            {"co": "이전회사", "ti": "선임", "s": "2012-01", "e": "2017-12"},
        ],
        "e": [{"sc": "서울대", "mj": "컴공", "dg": "학사"}],
        "c": [{"n": "정보처리기사", "acq": "2010.12"}],
        "conf": 0.9,
    }


def _one_project() -> dict:
    return {
        "pr": [
            {
                "n": "복구프로젝트-1",
                "cust": "고객-1",
                "s": "2020-01",
                "e": "2021-12",
                "resp": "PL 운영",
                "r": [{"d": "D1", "p": 1, "q": "복구프로젝트-1"}],
            }
        ]
    }


def _rich_page_text() -> str:
    from tests.test_analysis import _RICH_PAGE_TEXT

    return (
        _RICH_PAGE_TEXT
        + " KOSA 경력사항 ABC테크 2018.01~2024.12 책임 "
        + "이전회사 2012.01~2017.12 선임 서울대 컴퓨터공학 학사 "
        + "정보처리기사 AD SEP 복구프로젝트-1"
    )


# ---------------------------------------------------------------------------
# Registry / immutability / PROJECTS parity
# ---------------------------------------------------------------------------


def test_profile_extract_registry_v16_remains_resolvable() -> None:
    """v16 stays resolvable unchanged; current may advance (v17+)."""
    from app.ai.prompts import profile_extract_v15 as v15
    from app.ai.prompts import profile_extract_v16 as v16
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v16_spec = resolve_profile_prompt("profile-extract-v16")
    assert v16_spec.prompt_version == "profile-extract-v16"
    assert v16_spec.extraction_mode == "staged"
    assert v16_spec.compact_protocol is True
    assert v16_spec.strict_relation_evidence is True
    assert v16_spec.derive_project_duration is True
    assert v16_spec.clear_catalog_code_customer is True
    assert v16_spec.promote_exact_catalog_codes is True
    assert v16_spec.backfill_exact_core_evidence is True
    assert v16_spec.backfill_exact_project_evidence is True
    assert v16_spec.validate_projects_recovery_root is True
    assert v16_spec.validate_core_structured_completeness is True
    assert v16.VALIDATE_CORE_STRUCTURED_COMPLETENESS is True
    assert v16.VALIDATE_PROJECTS_RECOVERY_ROOT is True
    assert v16_spec.core_system_prompt == v16.CORE_SYSTEM_PROMPT
    assert v16_spec.projects_system_prompt == v16.PROJECTS_SYSTEM_PROMPT

    v15_spec = resolve_profile_prompt("profile-extract-v15")
    assert v15_spec.prompt_version == "profile-extract-v15"
    assert v15_spec.validate_core_structured_completeness is False
    assert v15_spec.validate_projects_recovery_root is True
    assert not hasattr(v15, "VALIDATE_CORE_STRUCTURED_COMPLETENESS")
    assert v15_spec.core_system_prompt == v15.CORE_SYSTEM_PROMPT


def test_v15_prompt_file_byte_identical_to_base() -> None:
    path = "backend/app/ai/prompts/profile_extract_v15.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_BASE_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


def test_v16_projects_and_normal_core_match_v15() -> None:
    from app.ai.prompts import profile_extract_v15 as v15
    from app.ai.prompts import profile_extract_v16 as v16

    assert v16.PROJECTS_SYSTEM_PROMPT == v15.PROJECTS_SYSTEM_PROMPT
    assert v16.PROJECTS_SCHEMA_GUIDE == v15.PROJECTS_SCHEMA_GUIDE
    assert v16.CORE_SYSTEM_PROMPT == v15.CORE_SYSTEM_PROMPT
    assert v16.build_core_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v15.build_core_user_prompt(code_catalog="x", document_blocks="y")
    assert v16.build_projects_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v15.build_projects_user_prompt(code_catalog="x", document_blocks="y")
    assert v16.build_projects_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    ) == v15.build_projects_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )

    recovery = v16.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )
    assert "omitted structured sections" in recovery
    assert "employer history" in recovery
    assert "education" in recovery
    assert "certifications" in recovery
    assert recovery != v15.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )


def test_core_structured_entity_count_excludes_scalars_and_projects() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.service import (
        candidate_quality_score,
        core_structured_entity_count,
    )

    doc = ProfileCandidateDocument.model_validate(
        {
            "schema_version": "profile-candidate-v1",
            "profile": {
                "name": "홍길동",
                "technical_grade": "EXPERT",
                "affiliation_company": "ABC",
                "department": "AI",
                "current_title": "책임",
                "career_document_value": "특급",
            },
            "expertise": [{"code": "EXP-MGT", "raw_value": "사업관리"}],
            "projects": [
                {
                    "project_name": "P1",
                    "source_refs": [
                        {
                            "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                            "page_no": 1,
                            "quote_text": "P1",
                        }
                    ],
                }
            ],
        }
    )
    assert core_structured_entity_count(doc) == 1
    assert candidate_quality_score(doc) > 1


# ---------------------------------------------------------------------------
# Service: v16 structured-sparse recovery vs v15 compatibility
# ---------------------------------------------------------------------------


def _seed_codes(db_session) -> None:
    from tests.test_analysis import _ensure_analysis_code

    for code, ctype, name in (
        ("JOB-MGT-PL", "JOB", "PL"),
        ("TECH-SEC-AD", "TECH", "AD"),
        ("EXP-MGT", "EXP", "Management"),
    ):
        _ensure_analysis_code(db_session, code, ctype, name)


def test_v16_structured_sparse_core_triggers_recovery_then_succeeds(db_session):
    from app.modules.analysis.service import (
        AnalysisService,
        candidate_quality_score,
        core_structured_entity_count,
    )
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v16a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _seed_codes(db_session)
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-KOSA",
        doc_type_name="경력증명서",
        page_text=_rich_page_text(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v16",
    )

    # Probe initial payload shape via expand path used by normalize.
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    probe = normalize_candidate(
        {**expand_compact_core(_sparse_core_px_only(), alias_to_id={}), "projects": []},
        catalog={},
        allowed_documents={},
    )
    assert core_structured_entity_count(probe) == 1
    assert candidate_quality_score(probe) > 1

    llm = _StagedSequenceLLM(
        [_sparse_core_px_only(), _complete_core(), _one_project()]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    assert llm.log_contexts[1].get("recovery_retry") is True
    assert "omitted structured sections" in llm.user_prompts[1]
    db_session.refresh(run)
    assert len(run.candidate_json["employment_history"]) >= 2
    assert len(run.candidate_json["education"]) >= 1
    assert len(run.candidate_json["projects"]) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_v16_structured_sparse_recovery_still_sparse_fails(db_session):
    from app.modules.analysis.service import (
        AnalysisService,
        InsufficientCandidateError,
    )
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v16b_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _seed_codes(db_session)
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-PROFILE",
        doc_type_name="프로필",
        page_text=_rich_page_text(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v16",
    )
    llm = _StagedSequenceLLM(
        [_sparse_core_px_only(), _sparse_core_px_only()]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "FAILED"
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert run.error_message == InsufficientCandidateError.USER_MESSAGE
    assert llm.calls == 2
    assert llm.phases == ["core", "core"]
    _cleanup_person(db_session, person.id, admin.id)


def test_v15_same_sparse_core_does_not_use_structured_guard(db_session):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v15c_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _seed_codes(db_session)
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-KOSA",
        doc_type_name="경력증명서",
        page_text=_rich_page_text(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v15",
    )
    llm = _StagedSequenceLLM([_sparse_core_px_only(), _one_project()])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(not c.get("recovery_retry") for c in llm.log_contexts)
    db_session.refresh(run)
    assert run.candidate_json["employment_history"] == []
    assert len(run.candidate_json["expertise"]) == 1
    assert len(run.candidate_json["projects"]) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_short_or_non_rich_doc_skips_structured_guard(db_session):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v16s_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _seed_codes(db_session)
    # Non-rich DOC_TYPE: structured count alone must not force CORE recovery.
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-CERT",
        doc_type_name="자격증",
        page_text="자격증 사본 정보처리기사 " + ("x" * 600),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v16",
    )
    llm = _StagedSequenceLLM([_sparse_core_px_only(), _one_project()])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    status = service.run_analysis(run.id)
    # May REVIEWING or FAILED for other reasons, but must not CORE-recover.
    assert llm.phases[0] == "core"
    assert not any(
        phase == "core" and (llm.log_contexts[i].get("recovery_retry") is True)
        for i, phase in enumerate(llm.phases)
    )
    assert "core" == llm.phases[0]
    # Second call if any is projects, not core recovery.
    if llm.calls >= 2:
        assert llm.phases[1] == "projects"
    assert status in {"REVIEWING", "FAILED"}
    _cleanup_person(db_session, person.id, admin.id)
