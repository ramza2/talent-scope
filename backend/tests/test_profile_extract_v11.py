"""profile-extract-v11 recall + CORE structure hardening."""

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

_BASE_SHA = "8145d1d7a828c6bfaab3db5dd1de7a3e2912029a"
_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC}

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
    "EXP-INACTIVE": ("EXP", False),
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


def _ref(quote: str, page: int = 1) -> dict:
    return {"d": "D1", "p": page, "q": quote}


def _v11_core(**overrides: Any) -> dict:
    payload = {
        "p": {"n": "홍길동", "ti": "PL", "tg": "EXPERT", "ac": "-"},
        "j": [
            {
                "v": "JOB-MGT-PL",
                "t": "PRIMARY",
                "r": [_ref("PL 시스템운영")],
            },
            {
                "v": "JOB-MGT-PM",
                "t": "SECONDARY",
                "r": [_ref("PM 사업관리")],
            },
            {
                "v": "JOB-OPS-SYS",
                "t": "EXPERIENCE",
                "r": [_ref("시스템운영")],
            },
        ],
        "s": [
            {"v": "AD", "c": "TECH-SEC-AD", "r": [_ref("AD")]},
        ],
        "x": [
            {"v": "EXP-MGT", "r": [_ref("사업관리")]},
            {"v": "EXP-INFRA", "r": [_ref("정보시스템 운영")]},
            {"v": "EXP-SEC-OPS", "r": [_ref("보안솔루션 운영")]},
        ],
        "w": [
            {
                "co": "회사A",
                "ti": "사원",
                "s": "2005.03",
                "e": "2008.02",
                "resp": "개발",
                "r": [_ref("회사A 2005.03")],
            },
            {
                "co": "회사B",
                "ti": "대리",
                "s": "2008.03",
                "e": "2011.06",
                "resp": "운영",
                "r": [_ref("회사B 2008.03")],
            },
            {
                "co": "회사C",
                "ti": "과장",
                "s": "2011.07",
                "e": "2014.12",
                "resp": "PL",
                "r": [_ref("회사C 2011.07")],
            },
            {
                "co": "회사D",
                "ti": "차장",
                "s": "2015.01",
                "e": "2018.08",
                "resp": "PM",
                "r": [_ref("회사D 2015.01")],
            },
            {
                "co": "회사E",
                "ti": "책임",
                "s": "2018.09",
                "e": "2022.12",
                "resp": "시스템운영",
                "r": [_ref("회사E 2018.09")],
            },
        ],
        "e": [
            {
                "sc": "○○고등학교",
                "dg": "고졸",
                "s": "1998.03",
                "e": "2001.02",
                "st": "졸업",
                "r": [_ref("○○고등학교")],
            },
            {
                "sc": "○○전문대학",
                "mj": "전산",
                "dg": "전문학사",
                "s": "2001.03",
                "e": "2003.02",
                "st": "졸업",
                "r": [_ref("○○전문대학")],
            },
            {
                "sc": "○○대학교",
                "mj": "컴퓨터공학",
                "dg": "학사",
                "s": "2003.03",
                "e": "2007.02",
                "st": "졸업",
                "r": [_ref("○○대학교")],
            },
            {
                "sc": "○○대학원",
                "mj": "정보보호",
                "dg": "석사",
                "s": "2012.03",
                "e": "2014.08",
                "st": "졸업",
                "r": [_ref("○○대학원")],
            },
        ],
        "c": [
            {
                "n": "정보처리산업기사",
                "is": "한국산업인력공단",
                "acq": "2010.12",
                "r": [_ref("정보처리산업기사")],
            },
            {
                "n": "정보보안기사",
                "is": "한국인터넷진흥원",
                "acq": "2013.06",
                "r": [_ref("정보보안기사")],
            },
            {
                "n": "SQLD",
                "is": "한국데이터산업진흥원",
                "acq": "2016.03",
                "r": [_ref("SQLD")],
            },
        ],
        "conf": 0.9,
    }
    payload.update(overrides)
    return payload


def _v11_projects() -> dict:
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
                "resp": "정보시스템 운영",
                "j": ["JOB-OPS-SYS"],
                "x": ["EXP-INFRA"],
                "r": [_ref("정보시스템운영프로젝트")],
                "rm": {
                    "j": [_ref("정보시스템 운영")],
                    "x": [_ref("정보시스템 운영")],
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
                "rm": {
                    "x": [_ref("정보보호 강화 구축, 사업관리")],
                },
            },
            {
                "n": "현대기아보안운영",
                "cust": "현대오토에버",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "PL 운영 AD SEP NAC",
                "j": ["JOB-MGT-PL"],
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "x": ["EXP-SEC-OPS"],
                "r": [_ref("현대기아보안운영")],
                "rm": {
                    "j": [_ref("PL 운영")],
                    "t": [_ref("AD, SEP, NAC")],
                    "x": [_ref("보안솔루션 운영")],
                },
            },
        ]
    }


def _v11_page_text() -> str:
    from tests.test_analysis import _RICH_PAGE_TEXT

    return (
        _RICH_PAGE_TEXT
        + " PL 시스템운영 PM 사업관리 시스템운영 AD SEP NAC"
        + " 사업관리 정보시스템 운영 보안솔루션 운영"
        + " 회사A 2005.03 회사B 2008.03 회사C 2011.07 회사D 2015.01 회사E 2018.09"
        + " ○○고등학교 ○○전문대학 ○○대학교 ○○대학원"
        + " 정보처리산업기사 정보보안기사 SQLD"
        + " PM사업관리프로젝트 정보시스템운영프로젝트 정보보호강화구축 현대기아보안운영"
        + " 정보보호 강화 구축, 사업관리 PL 운영 AD, SEP, NAC"
    )


def _normalize_core(compact: dict, *, catalog: dict | None = None, page_texts=None):
    from app.modules.analysis.compact_v8 import (
        expand_compact_core,
        promote_exact_root_catalog_codes,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(compact, alias_to_id=_ALIAS)
    doc = normalize_candidate(
        expanded,
        catalog=catalog if catalog is not None else _CATALOG,
        allowed_documents={_DOC: {1, 2}},
        page_texts=page_texts or {(_DOC, 1): _v11_page_text()},
    )
    return promote_exact_root_catalog_codes(doc, catalog=catalog or _CATALOG)


def _normalize_projects(compact: dict, *, catalog: dict | None = None, page_texts=None):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=catalog or _CATALOG,
    )
    doc = normalize_candidate(
        expanded,
        catalog=catalog or _CATALOG,
        allowed_documents={_DOC: {1, 2}},
        page_texts=page_texts or {(_DOC, 1): _v11_page_text()},
    )
    return apply_normalized_quote_evidence(doc)


# ---------------------------------------------------------------------------
# A. Registry / history
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v11() -> None:
    from app.ai.prompts import profile_extract_v10 as v10
    from app.ai.prompts import profile_extract_v11 as v11
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v11"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v11"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.derive_project_duration is True
    assert cur.clear_catalog_code_customer is True
    assert cur.promote_exact_catalog_codes is True
    assert cur.core_system_prompt == v11.CORE_SYSTEM_PROMPT
    assert v11.PROMOTE_EXACT_CATALOG_CODES is True

    v10_spec = resolve_profile_prompt("profile-extract-v10")
    assert v10_spec.promote_exact_catalog_codes is False
    assert v10_spec.strict_relation_evidence is True
    assert v10_spec.core_system_prompt == v10.CORE_SYSTEM_PROMPT


def test_v1_through_v10_prompt_files_byte_identical_to_base() -> None:
    for ver in range(1, 11):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(
            base
        ).digest(), path


def test_v11_prompt_mentions_core_and_project_recall_rules() -> None:
    from app.ai.prompts import profile_extract_v11 as v11

    core = v11.CORE_SYSTEM_PROMPT
    projects = v11.PROJECTS_SYSTEM_PROMPT
    assert "EMPLOYMENT periods" in core or "근무기간" in core
    assert "Never put dates" in core or "2014년 8월" in core
    assert "placeholder" in core.lower() or "빈" in core or "Ignore blank" in core
    assert "JOB-MGT-PM" in projects
    assert "JOB-OPS-SYS" in projects
    assert "EXP-INFRA" in projects
    assert "Never omit a supported relation" in projects
    assert "generic 사업관리 must NOT produce PM/PL" in projects.lower() or (
        "NOT produce PM/PL" in projects
    )


# ---------------------------------------------------------------------------
# B. Exact-code promotion
# ---------------------------------------------------------------------------


def test_promote_exact_root_catalog_codes() -> None:
    from app.ai.schemas.profile_candidate import (
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidateDocument,
        SkillCandidate,
    )
    from app.modules.analysis.compact_v8 import promote_exact_root_catalog_codes

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        jobs=[JobCandidate(raw_value="JOB-MGT-PL", code=None)],
        skills=[SkillCandidate(raw_value="TECH-SEC-AD", code=None)],
        expertise=[
            ExpertiseCandidate(raw_value="EXP-MGT", code=None),
            ExpertiseCandidate(raw_value="EXP-INFRA", code=None),
            ExpertiseCandidate(raw_value="EXP-SEC-OPS", code=None),
            # wrong type: EXP code as TECH skill already covered; JOB as EXP
            ExpertiseCandidate(raw_value="JOB-MGT-PM", code=None),
            ExpertiseCandidate(raw_value="EXP-INACTIVE", code=None),
            ExpertiseCandidate(raw_value="EXP-UNKNOWN", code=None),
            ExpertiseCandidate(raw_value="사업관리", code=None),
        ],
    )
    out = promote_exact_root_catalog_codes(doc, catalog=_CATALOG)
    assert out.jobs[0].code == "JOB-MGT-PL"
    assert out.skills[0].code == "TECH-SEC-AD"
    assert out.expertise[0].code == "EXP-MGT"
    assert out.expertise[1].code == "EXP-INFRA"
    assert out.expertise[2].code == "EXP-SEC-OPS"
    assert out.expertise[3].code is None  # wrong type
    assert out.expertise[4].code is None  # inactive
    assert out.expertise[5].code is None  # unknown
    assert out.expertise[6].code is None  # alias/fuzzy not allowed


def test_v10_does_not_promote_exact_catalog_codes() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    assert resolve_profile_prompt("profile-extract-v10").promote_exact_catalog_codes is False
    expanded = expand_compact_core(
        {"x": [{"v": "EXP-MGT"}, {"v": "EXP-INFRA"}, {"v": "EXP-SEC-OPS"}]},
        alias_to_id={},
    )
    doc = normalize_candidate(expanded, catalog=_CATALOG, allowed_documents={})
    assert all(e.code is None for e in doc.expertise)
    assert [e.raw_value for e in doc.expertise] == [
        "EXP-MGT",
        "EXP-INFRA",
        "EXP-SEC-OPS",
    ]


def test_v11_core_path_promotes_exact_raw_codes() -> None:
    doc = _normalize_core(
        {
            "x": [
                {"v": "EXP-MGT"},
                {"v": "EXP-INFRA"},
                {"v": "EXP-SEC-OPS"},
            ],
            "j": [{"v": "JOB-MGT-PL"}],
        }
    )
    assert {e.code for e in doc.expertise} == {
        "EXP-MGT",
        "EXP-INFRA",
        "EXP-SEC-OPS",
    }
    assert doc.jobs[0].code == "JOB-MGT-PL"


# ---------------------------------------------------------------------------
# C. Employment
# ---------------------------------------------------------------------------


def test_v11_five_employment_rows_keep_documented_dates() -> None:
    doc = _normalize_core(_v11_core())
    assert len(doc.employment_history) == 5
    dates = [(e.start_date, e.end_date) for e in doc.employment_history]
    assert dates == [
        ("2005.03", "2008.02"),
        ("2008.03", "2011.06"),
        ("2011.07", "2014.12"),
        ("2015.01", "2018.08"),
        ("2018.09", "2022.12"),
    ]
    assert all(e.source_refs for e in doc.employment_history)

    projects = _normalize_projects(_v11_projects())
    project_dates = {(p.start_date, p.end_date) for p in projects.projects}
    employment_dates = set(dates)
    assert employment_dates.isdisjoint(project_dates)


# ---------------------------------------------------------------------------
# D. Education
# ---------------------------------------------------------------------------


def test_v11_education_levels_and_status_not_date() -> None:
    from app.ai.prompts import profile_extract_v11 as v11

    assert "Ignore blank" in v11.CORE_SYSTEM_PROMPT or "placeholder" in (
        v11.CORE_SCHEMA_GUIDE + v11.CORE_SYSTEM_PROMPT
    ).lower()

    # Fixture omits blank/template placeholder rows (prompt requirement).
    compact = _v11_core()
    assert all(row.get("sc") for row in compact["e"])

    doc = _normalize_core(compact)
    assert len(doc.education) == 4
    degrees = [e.degree for e in doc.education]
    assert degrees == ["고졸", "전문학사", "학사", "석사"]
    assert all(e.status == "졸업" for e in doc.education)
    assert doc.education[3].end_date == "2014.08"
    assert "2014" not in (doc.education[3].status or "")
    assert all(e.source_refs for e in doc.education)

    # Bad model output shape (date in st) is still mapped as-is by expander —
    # prompt forbids it; verify correct mapping path preferred by fixture.
    from app.modules.analysis.compact_v8 import expand_compact_core

    bad = expand_compact_core(
        {
            "e": [
                {
                    "sc": "○○대학원",
                    "e": "2014.08",
                    "st": "졸업",
                }
            ]
        },
        alias_to_id={},
    )
    assert bad["education"][0]["end_date"] == "2014.08"
    assert bad["education"][0]["status"] == "졸업"


# ---------------------------------------------------------------------------
# E. Certification
# ---------------------------------------------------------------------------


def test_v11_certifications_preserve_acq_and_issuer() -> None:
    doc = _normalize_core(_v11_core())
    assert len(doc.certifications) == 3
    assert [c.acquired_date for c in doc.certifications] == [
        "2010.12",
        "2013.06",
        "2016.03",
    ]
    assert [c.issuer for c in doc.certifications] == [
        "한국산업인력공단",
        "한국인터넷진흥원",
        "한국데이터산업진흥원",
    ]
    assert all(c.source_refs for c in doc.certifications)
    assert all(c.expiry_date is None for c in doc.certifications)


# ---------------------------------------------------------------------------
# F. Strict project relation happy path
# ---------------------------------------------------------------------------


def test_v11_strict_project_relation_happy_path() -> None:
    doc = _normalize_projects(_v11_projects())
    assert len(doc.projects) == 4

    by_name = {p.project_name: p for p in doc.projects}

    pm = by_name["PM사업관리프로젝트"]
    assert [j.code for j in pm.jobs] == ["JOB-MGT-PM"]
    assert [e.code for e in pm.expertise] == ["EXP-MGT"]
    assert pm.jobs[0].source_refs[0].quote_text
    assert pm.expertise[0].source_refs[0].quote_text

    ops = by_name["정보시스템운영프로젝트"]
    assert [j.code for j in ops.jobs] == ["JOB-OPS-SYS"]
    assert [e.code for e in ops.expertise] == ["EXP-INFRA"]

    build = by_name["정보보호강화구축"]
    assert {e.code for e in build.expertise} == {"EXP-SEC-BUILD", "EXP-MGT"}
    assert build.jobs == []
    assert build.skills == []

    sec = by_name["현대기아보안운영"]
    assert [j.code for j in sec.jobs] == ["JOB-MGT-PL"]
    assert [s.code for s in sec.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [e.code for e in sec.expertise] == ["EXP-SEC-OPS"]
    assert all(s.source_refs[0].quote_text for s in sec.skills)
    assert sec.customer_name == "현대오토에버"


# ---------------------------------------------------------------------------
# G. Cross-project contamination still blocked
# ---------------------------------------------------------------------------


def test_v11_cross_project_contamination_still_blocked() -> None:
    page = (
        "정보보호강화구축 정보보호 강화 구축, 사업관리 "
        "현대기아보안운영 PL 운영 AD, SEP, NAC 보안솔루션 운영"
    )
    compact = {
        "pr": [
            {
                "n": "정보보호강화구축",
                "s": "2015.07",
                "e": "2015.12",
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "j": ["JOB-MGT-PL"],
                "x": ["EXP-SEC-BUILD", "EXP-MGT"],
                "r": [_ref("정보보호강화구축")],
                "rm": {
                    "x": [_ref("정보보호 강화 구축, 사업관리")],
                    # Intentionally no rm.t / rm.j — must not borrow from other project
                },
            },
            {
                "n": "현대기아보안운영",
                "cust": "현대오토에버",
                "j": ["JOB-MGT-PL"],
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "x": ["EXP-SEC-OPS"],
                "r": [_ref("현대기아보안운영")],
                "rm": {
                    "j": [_ref("PL 운영")],
                    "t": [_ref("AD, SEP, NAC")],
                    "x": [_ref("보안솔루션 운영")],
                },
            },
        ]
    }
    doc = _normalize_projects(compact, page_texts={(_DOC, 1): page})
    p1, p2 = doc.projects
    assert p1.project_name == "정보보호강화구축"
    assert p1.skills == []
    assert p1.jobs == []
    assert {e.code for e in p1.expertise} == {"EXP-SEC-BUILD", "EXP-MGT"}
    assert [s.code for s in p2.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [j.code for j in p2.jobs] == ["JOB-MGT-PL"]


# ---------------------------------------------------------------------------
# H. Call budget
# ---------------------------------------------------------------------------


def test_v11_happy_path_two_calls(db_session):
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

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v11"
    admin = _create_user(
        db_session, login_id=f"v11_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
        page_text=_v11_page_text(),
    )
    run = _queue_run(db_session, person.id, document.id)
    assert run.prompt_version == "profile-extract-v11"

    llm = _StagedSequenceLLM([_v11_core(), _v11_projects()])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v11" for c in llm.log_contexts)
    assert "EMPLOYMENT" in llm.system_prompts[0] or "근무기간" in llm.system_prompts[0]
    assert "Never omit a supported relation" in llm.system_prompts[1]
    db_session.refresh(run)
    assert len(run.candidate_json["employment_history"]) == 5
    assert all(
        row.get("start_date") and row.get("end_date")
        for row in run.candidate_json["employment_history"]
    )
    assert len(run.candidate_json["education"]) == 4
    assert len(run.candidate_json["certifications"]) == 3
    assert len(run.candidate_json["jobs"]) >= 1
    assert {e["code"] for e in run.candidate_json["expertise"]} >= {
        "EXP-MGT",
        "EXP-INFRA",
        "EXP-SEC-OPS",
    }
    assert len(run.candidate_json["projects"]) == 4
    _cleanup_person(db_session, person.id, admin.id)


def test_v11_recovery_budget_max_three_calls(db_session):
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
        db_session, login_id=f"v11r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_v11_page_text(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v11",
    )
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _v11_core(),
            _v11_projects(),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    _cleanup_person(db_session, person.id, admin.id)
