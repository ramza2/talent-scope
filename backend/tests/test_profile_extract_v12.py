"""profile-extract-v12 semantic accuracy hardening."""

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

_BASE_SHA = "d8d502f846085c12c6a6dd490a14157636c8ad68"
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


def _v12_mixed_source_page() -> str:
    from tests.test_analysis import _RICH_PAGE_TEXT

    return (
        _RICH_PAGE_TEXT
        + " 경력사항 직장명 근무기간 "
        + "알파소프트 2005.03~2008.02 사원 "
        + "베타시스템 2008.03~2011.06 대리 "
        + "감마테크 2011.07~2014.12 과장 "
        + "델타소프트 2015.01~2018.08 차장 "
        + "엡실론IT 2018.09~2022.12 책임 "
        + "경력기술서 프로젝트 "
        + "고객기관A 시스템 유지보수 2023.02~2024.09 PM "
        + "고객기관B 정보시스템 운영 2018.01~2018.12 "
        + "학력사항 고교 전문학사 학사 석사 재학중 박사 "
        + "○○고등학교 1998.03~2001.02 졸업 "
        + "○○전문대학 전산 2001.03~2003.02 졸업 "
        + "○○대학교 컴퓨터공학 2003.03~2007.02 졸업 "
        + "○○대학원 정보보호 2020.03~ 재학중 "
        + "010-1234-5678 test@example.com 광주광역시 서구 ○○로 123 "
        + "정보처리산업기사 한국산업인력공단 2010.12 "
        + "정보보안기사 한국인터넷진흥원 2013.06 "
        + "SQLD 한국데이터산업진흥원 2016.03 "
        + "PM 사업관리 PL 시스템운영 정보시스템 운영 "
        + "보안솔루션 운영(AD,SEP,NAC 등) AD SEP NAC "
        + "정보보호 강화 구축, 사업관리 정보보안시스템 운영 "
        + "PM사업관리프로젝트 정보시스템운영프로젝트 정보보호강화구축 "
        + "현대기아보안운영 PL 운영 AD, SEP, NAC"
    )


def _v12_core(**overrides: Any) -> dict:
    """Correct v12 extraction shape for mixed employer+project source."""
    payload = {
        "p": {
            "n": "홍길동",
            "ti": "PL",
            "tg": "EXPERT",
            "ac": "-",
            "ph": "010-1234-5678",
            "em": "test@example.com",
            "ar": "광주광역시",
            "r": {
                "phone": [_ref("010-1234-5678")],
                "email": [_ref("test@example.com")],
                "address_region": [_ref("광주광역시")],
            },
        },
        "j": [
            {
                "v": "JOB-MGT-PL",
                "c": "JOB-MGT-PL",
                "t": "PRIMARY",
                "r": [_ref("PL 시스템운영")],
            },
            {
                "v": "JOB-MGT-PM",
                "c": "JOB-MGT-PM",
                "t": "SECONDARY",
                "r": [_ref("PM 사업관리")],
            },
            {
                "v": "JOB-OPS-SYS",
                "c": "JOB-OPS-SYS",
                "t": "EXPERIENCE",
                "r": [_ref("시스템운영")],
            },
        ],
        "s": [
            {"v": "AD", "c": "TECH-SEC-AD", "r": [_ref("AD")]},
        ],
        "x": [
            {"v": "사업관리", "c": "EXP-MGT", "r": [_ref("사업관리")]},
            {"v": "정보시스템 운영", "c": "EXP-INFRA", "r": [_ref("정보시스템 운영")]},
            {"v": "보안 운영", "c": "EXP-SEC-OPS", "r": [_ref("보안솔루션 운영")]},
        ],
        # Employer table — NOT project customers / project end dates
        "w": [
            {
                "co": "알파소프트",
                "ti": "사원",
                "s": "2005.03",
                "e": "2008.02",
                "r": [_ref("알파소프트 2005.03~2008.02")],
            },
            {
                "co": "베타시스템",
                "ti": "대리",
                "s": "2008.03",
                "e": "2011.06",
                "r": [_ref("베타시스템 2008.03~2011.06")],
            },
            {
                "co": "감마테크",
                "ti": "과장",
                "s": "2011.07",
                "e": "2014.12",
                "r": [_ref("감마테크 2011.07~2014.12")],
            },
            {
                "co": "델타소프트",
                "ti": "차장",
                "s": "2015.01",
                "e": "2018.08",
                "r": [_ref("델타소프트 2015.01~2018.08")],
            },
            {
                "co": "엡실론IT",
                "ti": "책임",
                "s": "2018.09",
                "e": "2022.12",
                "r": [_ref("엡실론IT 2018.09~2022.12")],
            },
        ],
        # 4 real education rows; blank doctorate omitted; ongoing master no e
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
                "s": "2020.03",
                "st": "재학중",
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


def _v12_projects(*, multi_doc: bool = True) -> dict:
    sec_rm_t = (
        [_ref("보안솔루션 운영(AD,SEP,NAC 등)", doc="D2")]
        if multi_doc
        else [_ref("보안솔루션 운영(AD,SEP,NAC 등)")]
    )
    sec_r = (
        [
            _ref("현대기아보안운영", doc="D1"),
            _ref("현대기아보안운영", doc="D2"),
        ]
        if multi_doc
        else [_ref("현대기아보안운영")]
    )
    sec_rm_j = (
        [_ref("PL 운영", doc="D1")] if multi_doc else [_ref("PL 운영")]
    )
    sec_rm_x = (
        [_ref("정보보안시스템 운영", doc="D1")]
        if multi_doc
        else [_ref("정보보안시스템 운영")]
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
                "r": sec_r,
                "rm": {
                    "j": sec_rm_j,
                    "t": sec_rm_t,
                    "x": sec_rm_x,
                },
            },
        ]
    }


def _v12_page_texts() -> dict[tuple[str, int], str]:
    d1 = (
        _v12_mixed_source_page()
        + " 현대기아보안운영 PL 운영 정보보안시스템 운영"
    )
    d2 = (
        "현대기아보안운영 정보보안 운영 PL "
        "보안솔루션 운영(AD,SEP,NAC 등) AD SEP NAC"
    )
    return {(_DOC1, 1): d1, (_DOC2, 1): d2}


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
        allowed_documents={_DOC1: {1}, _DOC2: {1}},
        page_texts=page_texts or _v12_page_texts(),
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
        allowed_documents={_DOC1: {1}, _DOC2: {1}},
        page_texts=page_texts or _v12_page_texts(),
    )
    return apply_normalized_quote_evidence(doc)


# ---------------------------------------------------------------------------
# A. Registry / history
# ---------------------------------------------------------------------------


def test_profile_extract_registry_v12_remains_staged_compact_historical() -> None:
    """v12 stays resolvable as historical staged+compact; current may advance."""
    from app.ai.prompts import profile_extract_v11 as v11
    from app.ai.prompts import profile_extract_v12 as v12
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v12_spec = resolve_profile_prompt("profile-extract-v12")
    assert v12_spec.extraction_mode == "staged"
    assert v12_spec.compact_protocol is True
    assert v12_spec.strict_relation_evidence is True
    assert v12_spec.derive_project_duration is True
    assert v12_spec.clear_catalog_code_customer is True
    assert v12_spec.promote_exact_catalog_codes is True
    assert v12_spec.backfill_exact_core_evidence is False
    assert v12_spec.core_system_prompt == v12.CORE_SYSTEM_PROMPT
    assert v12.PROMOTE_EXACT_CATALOG_CODES is True

    v11_spec = resolve_profile_prompt("profile-extract-v11")
    assert v11_spec.promote_exact_catalog_codes is True
    assert v11_spec.strict_relation_evidence is True
    assert v11_spec.backfill_exact_core_evidence is False
    assert v11_spec.core_system_prompt == v11.CORE_SYSTEM_PROMPT


def test_v1_through_v11_prompt_files_byte_identical_to_base() -> None:
    for ver in range(1, 12):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(
            base
        ).digest(), path


def test_v12_prompt_employment_education_pii_and_same_project_rules() -> None:
    from app.ai.prompts import profile_extract_v12 as v12

    core = v12.CORE_SYSTEM_PROMPT
    projects = v12.PROJECTS_SYSTEM_PROMPT
    assert "EMPLOYER" in core or "employer table" in core.lower()
    assert "never w.co=고객기관" in core.lower() or "never w.co=" in core
    assert "2024.09" in core
    assert "재학중" in core and "invent" in core.lower()
    assert "광주광역시" in core or "coarse" in core.lower()
    assert "street" in core.lower() or "상세주소" in core
    assert "identity match" in projects.lower() or "identity match" in (
        v12.PROJECTS_SCHEMA_GUIDE.lower()
    )
    assert "JOB-OPS-SYS" in projects
    assert "Never omit a supported relation" in projects


# ---------------------------------------------------------------------------
# B. Employer vs customer contamination
# ---------------------------------------------------------------------------


def test_v12_employment_uses_employer_not_project_customer() -> None:
    doc = _normalize_core(_v12_core())
    assert len(doc.employment_history) == 5
    employers = [e.company_name for e in doc.employment_history]
    assert employers == [
        "알파소프트",
        "베타시스템",
        "감마테크",
        "델타소프트",
        "엡실론IT",
    ]
    assert "고객기관A" not in employers
    assert "고객기관B" not in employers
    assert "현대오토에버" not in employers
    ends = [e.end_date for e in doc.employment_history]
    assert "2024.09" not in ends  # project end must not leak
    assert ends == ["2008.02", "2011.06", "2014.12", "2018.08", "2022.12"]
    assert all(e.source_refs for e in doc.employment_history)

    # Prompt forbids contamination even when project dates overlap.
    from app.ai.prompts import profile_extract_v12 as v12

    assert "employer table" in v12.CORE_SYSTEM_PROMPT.lower() or (
        "경력사항" in v12.CORE_SYSTEM_PROMPT
    )


# ---------------------------------------------------------------------------
# C. Education
# ---------------------------------------------------------------------------


def test_v12_education_four_real_rows_no_invented_end_or_placeholder() -> None:
    from app.ai.prompts import profile_extract_v12 as v12

    assert "placeholder" in (
        v12.CORE_SCHEMA_GUIDE + v12.CORE_SYSTEM_PROMPT
    ).lower() or "박사" in v12.CORE_SYSTEM_PROMPT

    compact = _v12_core()
    assert len(compact["e"]) == 4
    assert all(row.get("sc") for row in compact["e"])
    assert "e" not in compact["e"][3]  # ongoing master: no invented end

    doc = _normalize_core(compact)
    assert len(doc.education) == 4
    assert [e.degree for e in doc.education] == ["고졸", "전문학사", "학사", "석사"]
    assert doc.education[3].status == "재학중"
    assert doc.education[3].end_date is None
    assert all(e.status != "2014년 8월" for e in doc.education)
    assert all(e.source_refs for e in doc.education)


# ---------------------------------------------------------------------------
# D. PII
# ---------------------------------------------------------------------------


def test_v12_pii_phone_email_coarse_address_region() -> None:
    from app.ai.prompts import profile_extract_v12 as v12

    assert "coarse" in (v12.CORE_SCHEMA_GUIDE + v12.CORE_SYSTEM_PROMPT).lower() or (
        "광주광역시" in v12.CORE_SCHEMA_GUIDE
    )
    assert "street" in (v12.CORE_SCHEMA_GUIDE + v12.CORE_SYSTEM_PROMPT).lower() or (
        "상세주소" in v12._SHARED_SAFETY
    )

    doc = _normalize_core(_v12_core())
    assert doc.profile.phone == "010-1234-5678"
    assert doc.profile.email == "test@example.com"
    assert doc.profile.address_region == "광주광역시"
    assert "서구" not in (doc.profile.address_region or "")
    assert "○○로" not in (doc.profile.address_region or "")
    assert "123" not in (doc.profile.address_region or "")


# ---------------------------------------------------------------------------
# E. Certification
# ---------------------------------------------------------------------------


def test_v12_certifications_preserve_issuer_and_acq() -> None:
    doc = _normalize_core(_v12_core())
    assert len(doc.certifications) == 3
    assert [c.certification_name for c in doc.certifications] == [
        "정보처리산업기사",
        "정보보안기사",
        "SQLD",
    ]
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
    assert all(c.expiry_date is None for c in doc.certifications)
    assert all(c.source_refs for c in doc.certifications)


# ---------------------------------------------------------------------------
# F. Root relations
# ---------------------------------------------------------------------------


def test_v12_root_jobs_and_expertise_with_evidence() -> None:
    doc = _normalize_core(_v12_core())
    job_codes = {j.code for j in doc.jobs}
    assert {"JOB-MGT-PM", "JOB-MGT-PL", "JOB-OPS-SYS"} <= job_codes
    assert all(j.source_refs and j.source_refs[0].quote_text for j in doc.jobs)

    exp_codes = {e.code for e in doc.expertise}
    assert {"EXP-MGT", "EXP-INFRA", "EXP-SEC-OPS"} <= exp_codes
    assert all(e.source_refs and e.source_refs[0].quote_text for e in doc.expertise)


# ---------------------------------------------------------------------------
# G. Same-engagement multi-document + no cross-project contamination
# ---------------------------------------------------------------------------


def test_v12_same_project_multi_doc_evidence_and_isolation() -> None:
    doc = _normalize_projects(_v12_projects())
    assert len(doc.projects) == 4
    by_name = {p.project_name: p for p in doc.projects}

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
    # Evidence combined across D1 (PL) and D2 (AD/SEP/NAC)
    skill_docs = {
        ref.document_id for s in sec.skills for ref in s.source_refs
    }
    assert _DOC2 in skill_docs
    job_docs = {ref.document_id for ref in sec.jobs[0].source_refs}
    assert _DOC1 in job_docs
    assert all(s.source_refs[0].quote_text for s in sec.skills)

    # Contamination: build must not gain neighbor TECH via missing rm
    page = (
        "정보보호강화구축 정보보호 강화 구축, 사업관리 "
        "현대기아보안운영 PL 운영 보안솔루션 운영(AD,SEP,NAC 등)"
    )
    contaminated = {
        "pr": [
            {
                "n": "정보보호강화구축",
                "t": ["TECH-SEC-AD"],
                "j": ["JOB-MGT-PL"],
                "x": ["EXP-SEC-BUILD", "EXP-MGT"],
                "r": [_ref("정보보호강화구축")],
                "rm": {"x": [_ref("정보보호 강화 구축, 사업관리")]},
            },
            {
                "n": "현대기아보안운영",
                "j": ["JOB-MGT-PL"],
                "t": ["TECH-SEC-AD"],
                "x": ["EXP-SEC-OPS"],
                "r": [_ref("현대기아보안운영")],
                "rm": {
                    "j": [_ref("PL 운영")],
                    "t": [_ref("보안솔루션 운영(AD,SEP,NAC 등)")],
                    "x": [_ref("현대기아보안운영")],
                },
            },
        ]
    }
    iso = _normalize_projects(
        contaminated,
        page_texts={(_DOC1, 1): page, (_DOC2, 1): page},
    )
    assert iso.projects[0].skills == []
    assert iso.projects[0].jobs == []
    assert [s.code for s in iso.projects[1].skills] == ["TECH-SEC-AD"]


# ---------------------------------------------------------------------------
# H. Call budget
# ---------------------------------------------------------------------------


def test_v12_happy_path_two_calls(db_session):
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
        db_session, login_id=f"v12_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
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
        page_text=_v12_mixed_source_page(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v12",
    )
    assert run.prompt_version == "profile-extract-v12"

    llm = _StagedSequenceLLM([_v12_core(), _v12_projects(multi_doc=False)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v12" for c in llm.log_contexts)
    assert "employer" in llm.system_prompts[0].lower() or (
        "경력사항" in llm.system_prompts[0]
    )
    assert "identity match" in llm.system_prompts[1].lower() or (
        "same project" in llm.system_prompts[1].lower()
    )
    db_session.refresh(run)
    assert len(run.candidate_json["employment_history"]) == 5
    employers = [e["company_name"] for e in run.candidate_json["employment_history"]]
    assert "고객기관A" not in employers
    assert len(run.candidate_json["education"]) == 4
    master = run.candidate_json["education"][3]
    assert master.get("end_date") in (None, "")
    assert run.candidate_json["profile"]["address_region"] == "광주광역시"
    assert len(run.candidate_json["certifications"]) == 3
    assert len(run.candidate_json["projects"]) == 4
    _cleanup_person(db_session, person.id, admin.id)


def test_v12_recovery_budget_max_three_calls(db_session):
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
        db_session, login_id=f"v12r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_v12_mixed_source_page(),
    )
    run = _queue_run(
        db_session,
        person.id,
        document.id,
        prompt_version="profile-extract-v12",
    )
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _v12_core(),
            _v12_projects(multi_doc=False),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    _cleanup_person(db_session, person.id, admin.id)
