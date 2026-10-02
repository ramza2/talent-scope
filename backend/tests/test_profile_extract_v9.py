"""profile-extract-v9 compact PROJECTS duration key disambiguation."""

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

_BASE_SHA = "f9209addc4bb4f75277f2fa24d2eaa3db0c4cb1e"


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


def _compact_core_payload(**overrides: Any) -> dict:
    payload = {
        "p": {"n": "홍길동", "ac": "ABC테크", "tg": "EXPERT"},
        "j": [{"v": "AI개발자", "c": "JOB-AI-DEV", "t": "PRIMARY"}],
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
        "conf": 0.85,
    }
    payload.update(overrides)
    return payload


def _compact_projects_mo(count: int = 7) -> dict:
    return {
        "pr": [
            {
                "n": f"프로젝트-{i}",
                "cu": f"고객-{i}",
                "s": f"201{i % 10}-01",
                "e": f"201{i % 10}-12",
                "mo": 12,
                "resp": "PL 운영",
                "t": ["TECH-SEC-AD"],
                "r": [{"d": "D1", "p": 1, "q": f"프로젝트-{i}"}],
            }
            for i in range(1, count + 1)
        ]
    }


# ---------------------------------------------------------------------------
# A. Registry
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v9_compact() -> None:
    from app.ai.prompts import profile_extract_v8 as v8
    from app.ai.prompts import profile_extract_v9 as v9
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v9"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v9"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True

    v8_spec = resolve_profile_prompt("profile-extract-v8")
    assert v8_spec.extraction_mode == "staged"
    assert v8_spec.compact_protocol is True
    assert v8_spec.core_system_prompt == v8.CORE_SYSTEM_PROMPT

    v9_spec = resolve_profile_prompt("profile-extract-v9")
    assert v9_spec.core_system_prompt == v9.CORE_SYSTEM_PROMPT
    assert v9.COMPACT_PROTOCOL is True


def test_v1_through_v8_prompt_files_unchanged_from_base() -> None:
    for ver in range(1, 9):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest(), path


# ---------------------------------------------------------------------------
# B. v9 project schema
# ---------------------------------------------------------------------------


def test_v9_projects_schema_uses_mo_not_root_d() -> None:
    from app.ai.prompts import profile_extract_v9 as v9

    guide = v9.PROJECTS_SCHEMA_GUIDE
    system = v9.PROJECTS_SYSTEM_PROMPT
    assert "mo = duration_months" in guide or "mo = duration_months" in guide
    assert "mo" in guide
    assert "MUST NOT use key d" in guide or "MUST NOT use key d" in system
    assert 'ref objects: {"d":"D1"' in guide or '{"d":"D1"' in guide
    # Duration key in schema guide is mo, not bare root d among field list.
    assert "n,cu,s,e,mo,resp" in guide.replace(" ", "")
    assert "Project root MUST NOT use key d" in system or "MUST NOT use key d" in guide
    assert "d=alias inside r[] only" in system or "ONLY inside ref objects" in guide


# ---------------------------------------------------------------------------
# C. Project adapter duration rules
# ---------------------------------------------------------------------------


def test_project_duration_mo_and_legacy_d_rules() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects

    def duration_of(raw_project: dict) -> int | None:
        expanded = expand_compact_projects({"pr": [raw_project]}, alias_to_id={})
        proj = expanded["projects"][0]
        return proj.get("duration_months")

    assert duration_of({"n": "P", "mo": 32}) == 32
    assert duration_of({"n": "P", "mo": "32"}) == 32
    assert duration_of({"n": "P", "mo": "D1"}) is None
    assert duration_of({"n": "P", "d": 32}) == 32
    assert duration_of({"n": "P", "d": "32"}) == 32
    assert duration_of({"n": "P", "d": "D1"}) is None
    assert duration_of({"n": "P", "mo": True}) is None
    assert duration_of({"n": "P", "mo": [32]}) is None
    assert duration_of({"n": "P", "mo": {"x": 1}}) is None
    assert duration_of({"n": "P", "d": True}) is None
    assert duration_of({"n": "P", "d": []}) is None
    assert duration_of({"n": "P", "d": {}}) is None
    # mo preferred; invalid mo does not fall back to legacy d.
    assert duration_of({"n": "P", "mo": "D1", "d": 32}) is None
    assert duration_of({"n": "P", "mo": 10, "d": "D1"}) == 10


# ---------------------------------------------------------------------------
# D. Production regression: root d="D1" on 7 projects
# ---------------------------------------------------------------------------


def test_production_root_d_alias_duration_omitted_seven_projects() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    compact = {
        "pr": [
            {
                "n": f"프로젝트-{i}",
                "cu": f"고객-{i}",
                "s": f"201{i}-01",
                "e": f"201{i}-12",
                "d": "D1",
                "resp": "PL 운영",
                "t": ["TECH-SEC-AD"],
                "r": [{"d": "D1", "p": 1, "q": f"프로젝트-{i}"}],
            }
            for i in range(1, 8)
        ]
    }
    expanded = expand_compact_projects(compact, alias_to_id={"D1": doc_id})
    assert len(expanded["projects"]) == 7
    for proj in expanded["projects"]:
        assert "duration_months" not in proj
        assert proj["source_refs"][0]["document_id"] == doc_id

    ProfileCandidateDocument.model_validate(expanded)
    page = " ".join(f"프로젝트-{i}" for i in range(1, 8))
    doc = normalize_candidate(
        expanded,
        catalog={"TECH-SEC-AD": ("TECH", True)},
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): page},
    )
    assert len(doc.projects) == 7
    assert all(p.duration_months is None for p in doc.projects)


# ---------------------------------------------------------------------------
# E / F. Happy path with mo + call budget
# ---------------------------------------------------------------------------


def test_v9_happy_path_seven_projects_with_mo(db_session):
    from app.ai.prompts.profile_extract import CURRENT_PROFILE_PROMPT_VERSION
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

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v9"
    admin = _create_user(
        db_session, login_id=f"v9_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "Active Directory")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT + " AD SEP NAC",
    )
    run = _queue_run(db_session, person.id, document.id)
    assert run.prompt_version == "profile-extract-v9"

    llm = _StagedSequenceLLM([_compact_core_payload(), _compact_projects_mo(7)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v9" for c in llm.log_contexts)
    assert "mo" in llm.system_prompts[1]
    assert "MUST NOT use key d" in llm.system_prompts[1]

    db_session.refresh(run)
    projects = run.candidate_json["projects"]
    assert len(projects) == 7
    assert all(p.get("duration_months") == 12 for p in projects)
    _cleanup_person(db_session, person.id, admin.id)


def test_v9_call_budget_two_primary_calls(db_session):
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
        db_session, login_id=f"v9b_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(db_session, person.id, document.id)
    llm = _StagedSequenceLLM([_compact_core_payload(), _compact_projects_mo(3)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    _cleanup_person(db_session, person.id, admin.id)
