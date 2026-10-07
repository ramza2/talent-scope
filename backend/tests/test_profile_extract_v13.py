"""profile-extract-v13 final semantic/evidence hardening."""

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

_BASE_SHA = "2093b190b245c5dd25cb3b713410a5421e13c4ac"
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ALIAS = {"D1": _DOC1, "D2": _DOC2}

_CATALOG: dict[str, tuple[str, bool]] = {
    "JOB-MGT-PM": ("JOB", True),
    "JOB-MGT-PL": ("JOB", True),
    "JOB-OPS-SYS": ("JOB", True),
    "TECH-SEC-AD": ("TECH", True),
    "TECH-SEC-SEP": ("TECH", True),
    "TECH-SEC-NAC": ("TECH", True),
    "EXP-MGT": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "EXP-SEC-OPS": ("EXP", True),
    "EXP-SEC-BUILD": ("EXP", True),
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


def _ref(quote: str, *, doc: str = "D1", page: int = 1) -> dict:
    return {"d": doc, "p": page, "q": quote}


def _v13_page() -> str:
    from tests.test_analysis import _RICH_PAGE_TEXT

    return (
        _RICH_PAGE_TEXT
        + " 홍길동 PL 010-1234-5678 test@example.com 광주광역시 "
        + "총경력 15년 PM 사업관리 시스템 운영 "
        + "알파소프트 베타시스템 "
        + "○○고등학교 ○○전문대학 ○○대학교 ○○대학원 "
        + "정보처리산업기사 정보보안기사 SQLD "
        + "AD SEP NAC 정보시스템 운영 보안솔루션 운영 "
        + "정보보호강화구축 정보보호 강화 구축, 사업관리 "
        + "고객보안운영프로젝트 PL 운영 정보보안시스템 운영 "
        + "보안솔루션 운영(AD,SEP,NAC 등) "
        + "정보시스템운영프로젝트 운영 "
        + "PM사업관리프로젝트"
    )


def _v13_page_texts() -> dict[tuple[str, int], str]:
    d1 = (
        _v13_page()
        + " 고객보안운영프로젝트 PL 운영 정보보안시스템 운영"
    )
    d2 = (
        "고객보안운영프로젝트 정보보안 운영 PL "
        "보안솔루션 운영(AD,SEP,NAC 등) AD SEP NAC"
    )
    return {(_DOC1, 1): d1, (_DOC2, 1): d2}


def _v13_core_empty_refs(**overrides: Any) -> dict:
    """CORE shape with empty refs — server backfill should fill exact matches."""
    payload = {
        "p": {
            "n": "홍길동",
            "ti": "PL",
            "tg": "EXPERT",
            "ph": "010-1234-5678",
            "em": "test@example.com",
            "ar": "광주광역시",
            "cdv": "총경력 15년",
            # ac omitted — current 소속 was "-"
        },
        "j": [
            {"v": "PM", "c": "JOB-MGT-PM", "t": "SECONDARY"},
            {"v": "PL", "c": "JOB-MGT-PL", "t": "PRIMARY"},
            {"v": "시스템 운영", "c": "JOB-OPS-SYS", "t": "EXPERIENCE"},
        ],
        "s": [
            {"v": "AD", "c": "TECH-SEC-AD"},
            {"v": "SEP", "c": "TECH-SEC-SEP"},
            {"v": "NAC", "c": "TECH-SEC-NAC"},
        ],
        "x": [
            {"v": "사업관리", "c": "EXP-MGT"},
            {"v": "정보시스템 운영", "c": "EXP-INFRA"},
            {"v": "보안 운영", "c": "EXP-SEC-OPS"},
        ],
        "w": [
            {"co": "알파소프트", "s": "2005.03", "e": "2008.02"},
            {"co": "베타시스템", "s": "2008.03", "e": "2011.06"},
        ],
        "e": [
            {
                "sc": "○○고등학교",
                "dg": "고졸",
                "s": "1998.03",
                "e": "2001.02",
                "st": "졸업",
            },
            {
                "sc": "○○전문대학",
                "mj": "전산",
                "dg": "전문학사",
                "s": "2001.03",
                "e": "2003.02",
                "st": "졸업",
            },
            {
                "sc": "○○대학교",
                "mj": "컴퓨터공학",
                "dg": "학사",
                "s": "2003.03",
                "e": "2007.02",
                "st": "졸업",
            },
            {
                "sc": "○○대학원",
                "mj": "정보보호",
                "dg": "석사",
                "s": "2020.03",
                "st": "재학중",
            },
        ],
        "c": [
            {
                "n": "정보처리산업기사",
                "is": "한국산업인력공단",
                "acq": "2010.12",
            },
            {
                "n": "정보보안기사",
                "is": "한국인터넷진흥원",
                "acq": "2013.06",
            },
            {"n": "SQLD", "is": "한국데이터산업진흥원", "acq": "2016.03"},
        ],
        "conf": 0.9,
    }
    payload.update(overrides)
    return payload


def _v13_projects(*, multi_doc: bool = True) -> dict:
    sec_rm_t = (
        [_ref("보안솔루션 운영(AD,SEP,NAC 등)", doc="D2")]
        if multi_doc
        else [_ref("보안솔루션 운영(AD,SEP,NAC 등)")]
    )
    sec_r = (
        [
            _ref("고객보안운영프로젝트", doc="D1"),
            _ref("고객보안운영프로젝트", doc="D2"),
        ]
        if multi_doc
        else [_ref("고객보안운영프로젝트")]
    )
    return {
        "pr": [
            {
                "n": "PM사업관리프로젝트",
                "cust": "고객PM",
                "s": "2019.01",
                "e": "2019.12",
                "resp": "PM 사업관리",
                "j": ["JOB-MGT-PM"],
                "x": ["EXP-MGT"],
                "r": [_ref("PM사업관리프로젝트")],
                "rm": {
                    "j": [_ref("PM 사업관리")],
                    "x": [_ref("PM 사업관리")],
                },
            },
            {
                "n": "정보시스템운영프로젝트",
                "cust": "고객OPS",
                "s": "2018.01",
                "e": "2018.12",
                "resp": "운영",
                "j": ["JOB-OPS-SYS"],
                "x": ["EXP-INFRA"],
                "r": [_ref("정보시스템운영프로젝트")],
                "rm": {
                    "j": [_ref("정보시스템운영프로젝트")],
                    "x": [_ref("정보시스템운영프로젝트")],
                },
            },
            {
                "n": "정보보호강화구축",
                "cust": "고객SEC",
                "s": "2015.07",
                "e": "2015.12",
                "resp": "정보보호 강화 구축, 사업관리",
                "x": ["EXP-SEC-BUILD", "EXP-MGT"],
                "r": [_ref("정보보호강화구축")],
                "rm": {"x": [_ref("정보보호 강화 구축, 사업관리")]},
            },
            {
                "n": "고객보안운영프로젝트",
                "cust": "고객기관보안",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "PL 운영",
                "j": ["JOB-MGT-PL"],
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "x": ["EXP-SEC-OPS"],
                "r": sec_r,
                "rm": {
                    "j": (
                        [_ref("PL 운영", doc="D1")]
                        if multi_doc
                        else [_ref("PL 운영")]
                    ),
                    "t": sec_rm_t,
                    "x": (
                        [_ref("정보보안시스템 운영", doc="D1")]
                        if multi_doc
                        else [_ref("정보보안시스템 운영")]
                    ),
                },
            },
        ]
    }


def _normalize_core(compact: dict, *, page_texts=None, backfill: bool = True):
    from app.modules.analysis.compact_v8 import (
        backfill_exact_core_evidence,
        expand_compact_core,
        promote_exact_root_catalog_codes,
    )
    from app.modules.analysis.normalize import normalize_candidate

    texts = page_texts or _v13_page_texts()
    allowed = {_DOC1: {1}, _DOC2: {1}}
    expanded = expand_compact_core(compact, alias_to_id=_ALIAS)
    doc = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents=allowed,
        page_texts=texts,
    )
    doc = promote_exact_root_catalog_codes(doc, catalog=_CATALOG)
    if backfill:
        doc = backfill_exact_core_evidence(
            doc, page_texts=texts, allowed_documents=allowed
        )
    return doc


def _normalize_projects(compact: dict, *, page_texts=None):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    texts = page_texts or _v13_page_texts()
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
        allowed_documents={_DOC1: {1}, _DOC2: {1}},
        page_texts=texts,
    )
    return apply_normalized_quote_evidence(doc)


# ---------------------------------------------------------------------------
# A. Registry / history
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v13() -> None:
    from app.ai.prompts import profile_extract_v12 as v12
    from app.ai.prompts import profile_extract_v13 as v13
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v13"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v13"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.derive_project_duration is True
    assert cur.clear_catalog_code_customer is True
    assert cur.promote_exact_catalog_codes is True
    assert cur.backfill_exact_core_evidence is True
    assert cur.core_system_prompt == v13.CORE_SYSTEM_PROMPT
    assert v13.BACKFILL_EXACT_CORE_EVIDENCE is True

    v12_spec = resolve_profile_prompt("profile-extract-v12")
    assert v12_spec.backfill_exact_core_evidence is False
    assert v12_spec.promote_exact_catalog_codes is True
    assert v12_spec.core_system_prompt == v12.CORE_SYSTEM_PROMPT


def test_v1_through_v12_prompt_files_byte_identical_to_base() -> None:
    for ver in range(1, 13):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(
            base
        ).digest(), path


def test_v13_prompt_school_affiliation_infra_rules() -> None:
    from app.ai.prompts import profile_extract_v13 as v13

    core = v13.CORE_SYSTEM_PROMPT + v13.CORE_SCHEMA_GUIDE
    projects = v13.PROJECTS_SYSTEM_PROMPT
    assert "NEVER" in core and "고교" in core
    assert "CURRENT" in core or "current" in core.lower()
    assert "EXP-INFRA" in core
    assert "identity match" in projects.lower()
    assert "시스템 운영" in projects or "정보시스템" in projects


# ---------------------------------------------------------------------------
# B. Exact CORE evidence backfill
# ---------------------------------------------------------------------------


def test_backfill_exact_core_evidence_happy_path() -> None:
    from app.ai.schemas.profile_candidate import (
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SourceRef,
    )
    from app.modules.analysis.compact_v8 import backfill_exact_core_evidence

    page = (
        "홍길동 PL 010-1234-5678 test@example.com 광주광역시 총경력 15년 "
        "PM 시스템 운영 AD SEP NAC 알파소프트 ○○고등학교 정보처리산업기사"
    )
    existing = SourceRef(document_id=_DOC1, page_no=1, quote_text="PM")
    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(
            name="홍길동",
            phone="010-1234-5678",
            email="test@example.com",
            address_region="광주광역시",
            current_title="PL",
            career_document_value="총경력 15년",
            affiliation_company="알파소프트",  # must NOT be backfilled
        ),
        jobs=[
            JobCandidate(raw_value="PM", code="JOB-MGT-PM", source_refs=[existing]),
            JobCandidate(raw_value="PL", code="JOB-MGT-PL"),
            JobCandidate(raw_value="시스템 운영", code="JOB-OPS-SYS"),
            JobCandidate(raw_value="없는직무값"),
        ],
        skills=[],
        expertise=[
            ExpertiseCandidate(raw_value="AD", code=None),
        ],
        employment_history=[],
        education=[],
        certifications=[],
        projects=[
            ProjectCandidate(project_name="P1", source_refs=[]),
        ],
    )
    # Attach remaining entities via model_copy after expand-like construction
    from app.ai.schemas.profile_candidate import (
        CertificationCandidate,
        EducationCandidate,
        EmploymentCandidate,
        SkillCandidate,
    )

    doc = doc.model_copy(
        update={
            "skills": [
                SkillCandidate(raw_value="AD", code="TECH-SEC-AD"),
                SkillCandidate(raw_value="SEP", code="TECH-SEC-SEP"),
                SkillCandidate(raw_value="NAC", code="TECH-SEC-NAC"),
            ],
            "expertise": [
                ExpertiseCandidate(raw_value="정보시스템 운영", code="EXP-INFRA"),
            ],
            "employment_history": [
                EmploymentCandidate(company_name="알파소프트"),
            ],
            "education": [
                EducationCandidate(school_name="○○고등학교", degree="고졸"),
            ],
            "certifications": [
                CertificationCandidate(certification_name="정보처리산업기사"),
            ],
        }
    )
    # Put 정보시스템 운영 in page for EXP
    page2 = page + " 정보시스템 운영"
    out = backfill_exact_core_evidence(
        doc,
        page_texts={(_DOC1, 1): page2},
        allowed_documents={_DOC1: {1}},
    )

    # Existing refs preserved
    assert out.jobs[0].source_refs[0].quote_text == "PM"
    # Exact backfill
    assert out.jobs[1].source_refs[0].quote_text == "PL"
    assert out.jobs[1].source_refs[0].document_id == _DOC1
    assert out.jobs[1].source_refs[0].page_no == 1
    assert out.jobs[2].source_refs[0].quote_text == "시스템 운영"
    # Non-exact: no ref
    assert out.jobs[3].source_refs == []
    assert [s.source_refs[0].quote_text for s in out.skills] == ["AD", "SEP", "NAC"]
    assert out.expertise[0].source_refs[0].quote_text == "정보시스템 운영"
    assert out.employment_history[0].source_refs[0].quote_text == "알파소프트"
    assert out.education[0].source_refs[0].quote_text == "○○고등학교"
    assert out.certifications[0].source_refs[0].quote_text == "정보처리산업기사"

    pref = out.profile.source_refs
    for field, quote in (
        ("name", "홍길동"),
        ("phone", "010-1234-5678"),
        ("email", "test@example.com"),
        ("address_region", "광주광역시"),
        ("current_title", "PL"),
        ("career_document_value", "총경력 15년"),
    ):
        assert pref[field][0].quote_text == quote
        assert pref[field][0].document_id == _DOC1
    # affiliation_company never backfilled
    assert "affiliation_company" not in pref
    # Projects untouched
    assert out.projects[0].source_refs == []


def test_backfill_skipped_when_flag_false_path() -> None:
    """Without backfill, empty refs stay empty after normalize+promote."""
    doc = _normalize_core(_v13_core_empty_refs(), backfill=False)
    assert all(not j.source_refs for j in doc.jobs)
    assert all(not e.source_refs for e in doc.employment_history)


def test_v13_core_path_backfills_empty_refs() -> None:
    doc = _normalize_core(_v13_core_empty_refs())
    assert all(j.source_refs and j.source_refs[0].quote_text for j in doc.jobs)
    assert all(s.source_refs for s in doc.skills)
    assert any(e.code == "EXP-INFRA" and e.source_refs for e in doc.expertise)
    assert all(w.source_refs for w in doc.employment_history)
    assert all(e.source_refs for e in doc.education)
    assert all(c.source_refs for c in doc.certifications)
    assert doc.profile.source_refs.get("name")
    assert "affiliation_company" not in (doc.profile.source_refs or {})


# ---------------------------------------------------------------------------
# C. Education semantic fixture
# ---------------------------------------------------------------------------


def test_v13_education_school_name_not_degree_label() -> None:
    from app.ai.prompts import profile_extract_v13 as v13

    assert "학교명" in v13.CORE_SCHEMA_GUIDE
    assert "NEVER" in v13.CORE_SCHEMA_GUIDE

    compact = _v13_core_empty_refs()
    # Fixture contract: actual institutions, not degree labels in sc
    assert [row["sc"] for row in compact["e"]] == [
        "○○고등학교",
        "○○전문대학",
        "○○대학교",
        "○○대학원",
    ]
    assert [row["dg"] for row in compact["e"]] == [
        "고졸",
        "전문학사",
        "학사",
        "석사",
    ]
    assert "e" not in compact["e"][3]
    doc = _normalize_core(compact)
    assert len(doc.education) == 4
    assert [e.school_name for e in doc.education] == [
        "○○고등학교",
        "○○전문대학",
        "○○대학교",
        "○○대학원",
    ]
    assert doc.education[3].end_date is None
    assert doc.education[3].status == "재학중"


# ---------------------------------------------------------------------------
# D. Affiliation
# ---------------------------------------------------------------------------


def test_v13_affiliation_omitted_when_current_blank() -> None:
    from app.ai.prompts import profile_extract_v13 as v13

    assert "CURRENT" in v13.CORE_SCHEMA_GUIDE or "omit ac" in v13.CORE_SCHEMA_GUIDE
    assert "historical" in v13.CORE_SCHEMA_GUIDE.lower() or (
        "Never promote" in v13.CORE_SCHEMA_GUIDE
    )

    doc = _normalize_core(_v13_core_empty_refs())
    assert doc.profile.affiliation_company is None
    assert len(doc.employment_history) >= 2
    assert doc.employment_history[-1].company_name == "베타시스템"


# ---------------------------------------------------------------------------
# E. Root expertise INFRA
# ---------------------------------------------------------------------------


def test_v13_root_exp_infra_with_system_ops() -> None:
    doc = _normalize_core(_v13_core_empty_refs())
    codes = {e.code for e in doc.expertise}
    assert "EXP-INFRA" in codes
    job_codes = {j.code for j in doc.jobs}
    assert "JOB-OPS-SYS" in job_codes
    infra = next(e for e in doc.expertise if e.code == "EXP-INFRA")
    assert infra.source_refs[0].quote_text == "정보시스템 운영"


# ---------------------------------------------------------------------------
# F. Projects
# ---------------------------------------------------------------------------


def test_v13_projects_system_ops_and_same_engagement_tech() -> None:
    doc = _normalize_projects(_v13_projects(multi_doc=True))
    by_name = {p.project_name: p for p in doc.projects}

    ops = by_name["정보시스템운영프로젝트"]
    assert [j.code for j in ops.jobs] == ["JOB-OPS-SYS"]
    assert [e.code for e in ops.expertise] == ["EXP-INFRA"]

    build = by_name["정보보호강화구축"]
    assert {e.code for e in build.expertise} == {"EXP-SEC-BUILD", "EXP-MGT"}
    assert build.jobs == []
    assert build.skills == []

    sec = by_name["고객보안운영프로젝트"]
    assert [j.code for j in sec.jobs] == ["JOB-MGT-PL"]
    assert [s.code for s in sec.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [e.code for e in sec.expertise] == ["EXP-SEC-OPS"]
    skill_docs = {ref.document_id for s in sec.skills for ref in s.source_refs}
    assert _DOC2 in skill_docs
    assert all(s.source_refs[0].quote_text for s in sec.skills)


# ---------------------------------------------------------------------------
# G. Budget / regression
# ---------------------------------------------------------------------------


def test_v13_happy_path_two_calls(db_session):
    from app.ai.prompts.profile_extract import CURRENT_PROFILE_PROMPT_VERSION
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _queue_run,
        _seed_person_doc,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v13"
    admin = _create_user(
        db_session, login_id=f"v13_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    for code, ctype, name in (
        ("JOB-MGT-PM", "JOB", "PM"),
        ("JOB-MGT-PL", "JOB", "PL"),
        ("JOB-OPS-SYS", "JOB", "시스템운영"),
        ("TECH-SEC-AD", "TECH", "AD"),
        ("TECH-SEC-SEP", "TECH", "SEP"),
        ("TECH-SEC-NAC", "TECH", "NAC"),
        ("EXP-MGT", "EXP", "Management"),
        ("EXP-INFRA", "EXP", "Infra"),
        ("EXP-SEC-OPS", "EXP", "Sec Ops"),
        ("EXP-SEC-BUILD", "EXP", "Sec Build"),
    ):
        _ensure_analysis_code(db_session, code, ctype, name)

    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_v13_page(),
    )
    run = _queue_run(db_session, person.id, document.id)
    assert run.prompt_version == "profile-extract-v13"

    llm = _StagedSequenceLLM(
        [_v13_core_empty_refs(), _v13_projects(multi_doc=False)]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v13" for c in llm.log_contexts)
    assert "r optional" in llm.system_prompts[0].lower() or (
        "optional" in llm.system_prompts[0].lower()
    )
    db_session.refresh(run)
    # Backfill should have filled CORE refs
    assert all(
        row.get("source_refs") for row in run.candidate_json["employment_history"]
    )
    assert any(
        e.get("code") == "EXP-INFRA" for e in run.candidate_json["expertise"]
    )
    assert run.candidate_json["profile"].get("affiliation_company") in (None, "")
    assert len(run.candidate_json["education"]) == 4
    assert run.candidate_json["education"][0]["school_name"] == "○○고등학교"
    assert len(run.candidate_json["projects"]) == 4
    _cleanup_person(db_session, person.id, admin.id)


def test_v13_recovery_budget_max_three_calls(db_session):
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v13r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_v13_page(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v13",
    )
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _v13_core_empty_refs(),
            _v13_projects(multi_doc=False),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    _cleanup_person(db_session, person.id, admin.id)
