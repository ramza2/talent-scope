"""profile-extract-v15 PROJECTS evidence/recovery hardening."""

from __future__ import annotations

import hashlib
import logging
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

_HEAD_SHA = "6973c29d4ba4d9bde1f1af19e5c7e08f6cf0154d"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC1}


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


def _v15_core(**overrides: Any) -> dict:
    payload = {
        "p": {"n": "홍길동", "tg": "EXPERT"},
        "j": [{"v": "PL", "c": "JOB-MGT-PL", "t": "PRIMARY"}],
        "s": [{"v": "AD", "c": "TECH-SEC-AD"}],
        "x": [{"v": "사업관리", "c": "EXP-MGT"}],
        "w": [{"co": "ABC테크", "ti": "책임", "s": "2010-01"}],
        "e": [{"sc": "서울대", "mj": "컴공", "dg": "학사"}],
        "c": [{"n": "정보처리산업기사", "acq": "2010.12"}],
        "conf": 0.9,
    }
    payload.update(overrides)
    return payload


def _projects_without_evidence(count: int = 5) -> dict:
    """Valid pr shape but no page-matching anchors/quotes → filter drops all.

    Avoid short anchors like \"운영\" that substring-match rich page text, or
    backfill_exact_project_evidence will repair and skip recovery.
    """
    return {
        "pr": [
            {
                "n": f"ZXQ없는프로젝트-{i}",
                "cust": f"ZXQ없는고객-{i}",
                "s": f"201{i % 10}-01",
                "e": f"201{i % 10}-12",
                "resp": f"ZXQ전용담당업무-{i}",
                "r": [{"d": "D1", "p": 1, "q": "ZXQ이인용문은페이지에없음"}],
            }
            for i in range(1, count + 1)
        ]
    }


def _bare_project_root() -> dict:
    """Malformed recovery: bare project object (server repro shape)."""
    return {
        "n": "복구프로젝트-1",
        "cust": "고객-1",
        "e": "2015-12",
        "j": ["JOB-MGT-PL"],
        "r": [{"d": "D1", "p": 1, "q": "복구프로젝트-1"}],
        "resp": "운영",
        "s": "2012-01",
        "x": ["EXP-MGT"],
    }


def _valid_projects_recovery(count: int = 1) -> dict:
    return {
        "pr": [
            {
                "n": f"복구프로젝트-{i}",
                "cust": f"고객-{i}",
                "s": f"201{i % 10}-01",
                "e": f"201{i % 10}-12",
                "resp": "운영",
                "r": [{"d": "D1", "p": 1, "q": f"복구프로젝트-{i}"}],
            }
            for i in range(1, count + 1)
        ]
    }


def _rich_page_text(count: int = 5) -> str:
    from tests.test_analysis import _RICH_PAGE_TEXT

    # Anchors only for valid recovery quotes — not for initial no-evidence rows.
    names = " ".join(f"복구프로젝트-{i}" for i in range(1, count + 1))
    return _RICH_PAGE_TEXT + " AD SEP NAC 시스템 운영 " + names


# ---------------------------------------------------------------------------
# A. Registry
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v15() -> None:
    from app.ai.prompts import profile_extract_v14 as v14
    from app.ai.prompts import profile_extract_v15 as v15
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v15"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v15"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.derive_project_duration is True
    assert cur.clear_catalog_code_customer is True
    assert cur.promote_exact_catalog_codes is True
    assert cur.backfill_exact_core_evidence is True
    assert cur.backfill_exact_project_evidence is True
    assert cur.validate_projects_recovery_root is True
    assert cur.core_system_prompt == v15.CORE_SYSTEM_PROMPT
    assert cur.projects_system_prompt == v15.PROJECTS_SYSTEM_PROMPT
    assert v15.VALIDATE_PROJECTS_RECOVERY_ROOT is True

    v14_spec = resolve_profile_prompt("profile-extract-v14")
    assert v14_spec.prompt_version == "profile-extract-v14"
    assert v14_spec.core_system_prompt == v14.CORE_SYSTEM_PROMPT
    assert v14_spec.projects_system_prompt == v14.PROJECTS_SYSTEM_PROMPT
    assert v14_spec.backfill_exact_project_evidence is True
    assert v14_spec.validate_projects_recovery_root is False
    assert not hasattr(v14, "VALIDATE_PROJECTS_RECOVERY_ROOT")


def test_v14_prompt_file_byte_identical_to_head() -> None:
    path = "backend/app/ai/prompts/profile_extract_v14.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_HEAD_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


# ---------------------------------------------------------------------------
# B. v15 PROJECTS contract
# ---------------------------------------------------------------------------


def test_v15_projects_contract_exact_root_and_ref_q() -> None:
    from app.ai.prompts import profile_extract_v14 as v14
    from app.ai.prompts import profile_extract_v15 as v15

    schema = v15.PROJECTS_SCHEMA_GUIDE
    system = v15.PROJECTS_SYSTEM_PROMPT
    user = v15.build_projects_user_prompt(
        code_catalog="JOB-MGT-PL",
        document_blocks="프로젝트-1",
    )
    recovery = v15.build_projects_user_prompt(
        code_catalog="JOB-MGT-PL",
        document_blocks="프로젝트-1",
        recovery_retry=True,
    )

    assert '{"pr":[...]}' in schema or '{"pr":[...]}' in system
    assert "Bare project" in schema or "bare project" in system.lower()
    assert '"d"' in schema and '"p"' in schema and '"q"' in schema
    assert "verbatim" in schema.lower() or "원문 그대로" in schema
    assert "paraphrase" in schema.lower() or "요약" in schema

    assert 'Root MUST be exactly {"pr":[...]}' in user
    assert "verbatim" in user.lower()
    assert '"d":"D1"' in user and '"q":"verbatim"' in user

    assert "Previous PROJECTS failed" in recovery
    assert '{"pr":[...]}' in recovery
    assert "verbatim q" in recovery
    assert "bare project" in recovery.lower()
    assert recovery.startswith("[RECOVERY]")

    # CORE behavior equivalent to v14
    assert v15.CORE_SYSTEM_PROMPT == v14.CORE_SYSTEM_PROMPT
    assert v15.CORE_SCHEMA_GUIDE == v14.CORE_SCHEMA_GUIDE
    assert v15.build_core_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v14.build_core_user_prompt(code_catalog="x", document_blocks="y")
    assert v15.PROJECTS_SYSTEM_PROMPT != v14.PROJECTS_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# E. Legitimate shape helper
# ---------------------------------------------------------------------------


def test_is_valid_compact_projects_root_shapes() -> None:
    from app.modules.analysis.compact_v8 import is_valid_compact_projects_root

    assert is_valid_compact_projects_root({"pr": []}) is True
    assert is_valid_compact_projects_root({"pr": [{"n": "x"}]}) is True
    assert is_valid_compact_projects_root({"projects": []}) is True
    assert is_valid_compact_projects_root({"projects": [{"name": "x"}]}) is True
    assert is_valid_compact_projects_root(_bare_project_root()) is False
    assert is_valid_compact_projects_root({"n": "x", "r": []}) is False
    assert is_valid_compact_projects_root({"pr": "nope"}) is False
    assert is_valid_compact_projects_root({}) is False
    assert is_valid_compact_projects_root(None) is False


# ---------------------------------------------------------------------------
# C / D. Malformed recovery fail-safe + valid recovery
# ---------------------------------------------------------------------------


def test_v15_malformed_projects_recovery_fails_closed(db_session, caplog):
    from app.modules.analysis.service import (
        AnalysisService,
        InsufficientCandidateError,
    )
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v15m_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "JOB-MGT-PL", "JOB", "PL")
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "AD")
    _ensure_analysis_code(db_session, "EXP-MGT", "EXP", "Management")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_rich_page_text(5),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v15",
    )

    llm = _StagedSequenceLLM(
        [
            _v15_core(),
            _projects_without_evidence(5),
            _bare_project_root(),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    with caplog.at_level(logging.INFO):
        status = service.run_analysis(run.id)
    assert status == "FAILED"
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert run.error_message == InsufficientCandidateError.USER_MESSAGE
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    assert llm.log_contexts[2].get("recovery_retry") is True
    assert "Previous PROJECTS failed" in llm.user_prompts[2]
    assert any(
        "analysis projects malformed root" in r.message for r in caplog.records
    )
    assert any("raw_top_level_keys=" in r.message for r in caplog.records)
    # Must not silently REVIEWING with projects=0
    assert run.candidate_json is None or run.status == "FAILED"
    _cleanup_person(db_session, person.id, admin.id)


def test_v14_bare_projects_recovery_keeps_legacy_runtime(db_session, caplog):
    """v14 must not apply the v15 malformed-root fail-closed guard."""
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v14m_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "JOB-MGT-PL", "JOB", "PL")
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "AD")
    _ensure_analysis_code(db_session, "EXP-MGT", "EXP", "Management")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_rich_page_text(5),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v14",
    )

    llm = _StagedSequenceLLM(
        [
            _v15_core(),
            _projects_without_evidence(5),
            _bare_project_root(),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    with caplog.at_level(logging.INFO):
        status = service.run_analysis(run.id)
    assert status == "REVIEWING"
    db_session.refresh(run)
    assert run.status == "REVIEWING"
    assert run.candidate_json["projects"] == []
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    assert llm.log_contexts[2].get("recovery_retry") is True
    assert not any(
        "analysis projects malformed root" in r.message for r in caplog.records
    )
    _cleanup_person(db_session, person.id, admin.id)


def test_v15_valid_projects_recovery_keeps_projects(db_session):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v15v_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "JOB-MGT-PL", "JOB", "PL")
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "AD")
    _ensure_analysis_code(db_session, "EXP-MGT", "EXP", "Management")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_rich_page_text(2),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v15",
    )

    llm = _StagedSequenceLLM(
        [
            _v15_core(),
            _projects_without_evidence(2),
            _valid_projects_recovery(2),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    db_session.refresh(run)
    assert run.status == "REVIEWING"
    assert len(run.candidate_json["projects"]) == 2
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    _cleanup_person(db_session, person.id, admin.id)


def test_v15_empty_pr_list_recovery_is_valid_shape_not_malformed(db_session):
    """Legitimate {"pr":[]} after recovery is valid shape (not bare-root fail)."""
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v15e_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "JOB-MGT-PL", "JOB", "PL")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_rich_page_text(1),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v15",
    )

    llm = _StagedSequenceLLM(
        [
            _v15_core(),
            _projects_without_evidence(1),
            {"pr": []},
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    # Empty projects after recovery: shape valid; may still fail sparse guard
    # if overall candidate is sparse — but must NOT be malformed-root path.
    status = service.run_analysis(run.id)
    db_session.refresh(run)
    assert llm.calls == 3
    assert llm.phases == ["core", "projects", "projects"]
    # With rich CORE payload, merged candidate should succeed REVIEWING.
    assert status == "REVIEWING"
    assert run.candidate_json["projects"] == []
    _cleanup_person(db_session, person.id, admin.id)
