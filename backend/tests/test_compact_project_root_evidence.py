"""v13 project root evidence exact backfill (quote-less root refs)."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ALIAS = {"D1": _DOC1, "D2": _DOC2}
_CATALOG = {
    "JOB-MGT-PL": ("JOB", True),
    "TECH-SEC-AD": ("TECH", True),
    "EXP-SEC-OPS": ("EXP", True),
}


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


def _pipeline(
    compact: dict,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed: dict[str, set[int]] | None = None,
    backfill: bool = True,
):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_exact_project_evidence,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=_CATALOG,
    )
    doc = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents=allowed or {_DOC1: {1, 2}, _DOC2: {1}},
        page_texts=page_texts,
    )
    repaired = 0
    if backfill:
        doc, repaired = backfill_exact_project_evidence(doc, page_texts=page_texts)
    filtered = apply_normalized_quote_evidence(doc)
    return filtered, repaired, doc


def test_registry_v13_enables_project_root_backfill_only() -> None:
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v13"
    v13 = resolve_profile_prompt("profile-extract-v13")
    assert v13.backfill_exact_project_evidence is True
    assert v13.backfill_exact_core_evidence is True
    assert v13.strict_relation_evidence is True

    for ver in (
        "profile-extract-v10",
        "profile-extract-v11",
        "profile-extract-v12",
    ):
        assert resolve_profile_prompt(ver).backfill_exact_project_evidence is False


def test_paraphrased_quote_repaired_via_exact_project_name() -> None:
    """Invalid/paraphrased q cleared by normalize; same-page project_name repairs."""
    page = "고객보안운영프로젝트 PL 운영 보안솔루션"
    compact = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "cust": "고객기관",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "PL 운영",
                "r": [{"d": "D1", "p": 1, "q": "의역된프로젝트인용문"}],
            }
        ]
    }
    filtered, repaired, before_filter = _pipeline(
        compact, page_texts={(_DOC1, 1): page}
    )
    assert repaired == 1
    assert before_filter.projects[0].source_refs[0].quote_text == "고객보안운영프로젝트"
    assert len(filtered.projects) == 1
    assert filtered.projects[0].project_name == "고객보안운영프로젝트"
    assert filtered.projects[0].source_refs[0].document_id == _DOC1
    assert filtered.projects[0].source_refs[0].page_no == 1


def test_customer_and_responsibility_fallback_anchors() -> None:
    page_cust = "발주 고객기관보안 관련 문서"
    compact_cust = {
        "pr": [
            {
                "n": "없는프로젝트명",
                "cust": "고객기관보안",
                "resp": "운영",
                "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
            }
        ]
    }
    filtered, repaired, before = _pipeline(
        compact_cust, page_texts={(_DOC1, 1): page_cust}
    )
    assert repaired == 1
    assert before.projects[0].source_refs[0].quote_text == "고객기관보안"
    assert len(filtered.projects) == 1

    page_resp = "본 과업은 정보시스템 통합운영 수행"
    compact_resp = {
        "pr": [
            {
                "n": "없는이름",
                "cust": "없는고객",
                "resp": "정보시스템 통합운영",
                "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
            }
        ]
    }
    filtered2, repaired2, before2 = _pipeline(
        compact_resp, page_texts={(_DOC1, 1): page_resp}
    )
    assert repaired2 == 1
    assert before2.projects[0].source_refs[0].quote_text == "정보시스템 통합운영"
    assert len(filtered2.projects) == 1


def test_existing_valid_quote_unchanged() -> None:
    page = "고객보안운영프로젝트 기존정확한인용 PL 운영"
    compact = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "r": [{"d": "D1", "p": 1, "q": "기존정확한인용"}],
            }
        ]
    }
    filtered, repaired, before = _pipeline(
        compact, page_texts={(_DOC1, 1): page}
    )
    assert repaired == 0
    assert before.projects[0].source_refs[0].quote_text == "기존정확한인용"
    assert len(filtered.projects) == 1


def test_empty_refs_unique_project_name_exact_match_repaired() -> None:
    from app.ai.schemas.profile_candidate import (
        ProfileCandidateDocument,
        ProjectCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_exact_project_evidence,
    )

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        projects=[
            ProjectCandidate(
                project_name="고객보안운영프로젝트",
                customer_name="고객기관",
                source_refs=[],
            )
        ],
    )
    texts = {
        (_DOC1, 1): "머리말",
        (_DOC1, 2): "고객보안운영프로젝트 PL 운영",
    }
    out, repaired = backfill_exact_project_evidence(doc, page_texts=texts)
    assert repaired == 1
    ref = out.projects[0].source_refs[0]
    assert ref.quote_text == "고객보안운영프로젝트"
    assert ref.document_id == _DOC1
    assert ref.page_no == 2
    filtered = apply_normalized_quote_evidence(out)
    assert len(filtered.projects) == 1


def test_empty_refs_customer_name_fallback_exact_match_repaired() -> None:
    from app.ai.schemas.profile_candidate import (
        ProfileCandidateDocument,
        ProjectCandidate,
    )
    from app.modules.analysis.compact_v8 import backfill_exact_project_evidence

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        projects=[
            ProjectCandidate(
                project_name="페이지에없는프로젝트명",
                customer_name="고객기관보안",
                responsibilities="운영",
                source_refs=[],
            )
        ],
    )
    out, repaired = backfill_exact_project_evidence(
        doc,
        page_texts={(_DOC1, 1): "발주 고객기관보안 관련 문서"},
    )
    assert repaired == 1
    assert out.projects[0].source_refs[0].quote_text == "고객기관보안"
    assert out.projects[0].source_refs[0].document_id == _DOC1
    assert out.projects[0].source_refs[0].page_no == 1


def test_empty_refs_ambiguous_duplicate_match_not_repaired() -> None:
    from app.ai.schemas.profile_candidate import (
        ProfileCandidateDocument,
        ProjectCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_exact_project_evidence,
    )

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        projects=[
            ProjectCandidate(
                project_name="공통프로젝트명",
                source_refs=[],
            )
        ],
    )
    texts = {
        (_DOC1, 1): "공통프로젝트명 첫번째",
        (_DOC1, 2): "공통프로젝트명 두번째",
    }
    out, repaired = backfill_exact_project_evidence(doc, page_texts=texts)
    assert repaired == 0
    assert out.projects[0].source_refs == []
    assert apply_normalized_quote_evidence(out).projects == []


def test_empty_refs_no_exact_scalar_not_repaired() -> None:
    from app.ai.schemas.profile_candidate import (
        ProfileCandidateDocument,
        ProjectCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_exact_project_evidence,
    )

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        projects=[
            ProjectCandidate(
                project_name="없는이름",
                customer_name="없는고객",
                responsibilities="없는업무",
                source_refs=[],
            )
        ],
    )
    out, repaired = backfill_exact_project_evidence(
        doc,
        page_texts={(_DOC1, 1): "관련 없는 페이지 텍스트"},
    )
    assert repaired == 0
    assert out.projects[0].source_refs == []
    assert apply_normalized_quote_evidence(out).projects == []


def test_wrong_page_or_no_anchor_remains_fail_closed() -> None:
    texts = {
        (_DOC1, 1): "다른내용만 있음",
        (_DOC1, 2): "고객보안운영프로젝트",
    }
    compact = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
            }
        ]
    }
    filtered, repaired, _before = _pipeline(
        compact,
        page_texts=texts,
        allowed={_DOC1: {1, 2}},
    )
    assert repaired == 0
    assert filtered.projects == []

    filtered2, repaired2, _ = _pipeline(
        {
            "pr": [
                {
                    "n": "완전다른이름",
                    "cust": "다른고객",
                    "resp": "다른업무",
                    "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
                }
            ]
        },
        page_texts={(_DOC1, 1): "아무 관련 없는 페이지"},
    )
    assert repaired2 == 0
    assert filtered2.projects == []


def test_never_borrow_evidence_from_another_document() -> None:
    texts = {
        (_DOC1, 1): " unrelated page ",
        (_DOC2, 1): "고객보안운영프로젝트 PL",
    }
    compact = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
            }
        ]
    }
    filtered, repaired, _ = _pipeline(compact, page_texts=texts)
    assert repaired == 0
    assert filtered.projects == []


def test_relation_jtx_behavior_unchanged() -> None:
    """Root repair must not resurrect j/t/x lacking rm quote evidence."""
    page = (
        "고객보안운영프로젝트 PL 운영 "
        "보안솔루션 운영(AD,SEP,NAC 등)"
    )
    compact = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "j": ["JOB-MGT-PL"],
                "t": ["TECH-SEC-AD"],
                "x": ["EXP-SEC-OPS"],
                "r": [{"d": "D1", "p": 1, "q": "의역루트인용"}],
                "rm": {
                    "j": [{"d": "D1", "p": 1, "q": "PL 운영"}],
                    "t": [{"d": "D1", "p": 1, "q": "없는TECH인용"}],
                    "x": [{"d": "D1", "p": 1, "q": "고객보안운영프로젝트"}],
                },
            }
        ]
    }
    filtered, repaired, _ = _pipeline(compact, page_texts={(_DOC1, 1): page})
    assert repaired == 1
    assert len(filtered.projects) == 1
    assert [j.code for j in filtered.projects[0].jobs] == ["JOB-MGT-PL"]
    assert filtered.projects[0].skills == []
    assert [e.code for e in filtered.projects[0].expertise] == ["EXP-SEC-OPS"]


def test_dates_alone_not_used_as_evidence_anchor() -> None:
    compact = {
        "pr": [
            {
                "n": "없는이름",
                "cust": "없는고객",
                "s": "2012.07",
                "e": "2015.02",
                "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
            }
        ]
    }
    filtered, repaired, _ = _pipeline(
        compact,
        page_texts={(_DOC1, 1): "기간 2012.07 ~ 2015.02 만 있는 페이지"},
    )
    assert repaired == 0
    assert filtered.projects == []


def test_v13_projects_path_logs_diagnostics(db_session, caplog):
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
    from tests.test_profile_extract_v13 import _StagedSequenceLLM, _v13_core_empty_refs

    admin = _create_user(
        db_session, login_id=f"prbf_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "JOB-MGT-PL", "JOB", "PL")
    page = (
        _RICH_PAGE_TEXT
        + " 홍길동 PL 고객보안운영프로젝트 정보보안시스템 운영"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=page,
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v13",
    )
    projects = {
        "pr": [
            {
                "n": "고객보안운영프로젝트",
                "j": ["JOB-MGT-PL"],
                "r": [{"d": "D1", "p": 1, "q": "의역된프로젝트인용"}],
                "rm": {"j": [{"d": "D1", "p": 1, "q": "PL"}]},
            }
        ]
    }
    llm = _StagedSequenceLLM([_v13_core_empty_refs(), projects])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    with caplog.at_level(logging.INFO, logger="app.modules.analysis.service"):
        assert service.run_analysis(run.id) == "REVIEWING"
    db_session.refresh(run)
    assert len(run.candidate_json["projects"]) == 1
    assert any(
        "analysis project root evidence" in r.message
        and "root_refs_repaired=" in r.message
        and "projects_after_filter=" in r.message
        for r in caplog.records
    )
    for r in caplog.records:
        if "analysis project root evidence" in r.message:
            assert "의역된프로젝트인용" not in r.message
            assert "고객보안운영프로젝트" not in r.message
    _cleanup_person(db_session, person.id, admin.id)
