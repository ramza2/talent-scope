"""profile-extract-v10 semantic accuracy hardening."""

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

_BASE_SHA = "17dce703b20f9faeb15e0a51ce3a3c1464154dbb"
_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC}


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


def _v10_core(**overrides: Any) -> dict:
    payload = {
        "p": {"n": "홍길동", "tg": "EXPERT"},
        "j": [{"v": "PL", "c": "JOB-PL", "t": "PRIMARY"}],
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
        "w": [
            {"co": f"회사-{i}", "ti": "책임", "s": f"201{i}-01"}
            for i in range(1, 6)
        ],
        "e": [{"sc": "서울대", "mj": "컴공", "dg": "학사"}],
        "c": [{"n": "정보처리산업기사", "acq": "2010.12"}],
        "conf": 0.9,
    }
    payload.update(overrides)
    return payload


def _v10_projects(count: int = 7) -> dict:
    return {
        "pr": [
            {
                "n": f"프로젝트-{i}",
                "cust": f"고객-{i}",
                "s": f"201{i % 10}-01",
                "e": f"201{i % 10}-12",
                "resp": "운영",
                "r": [{"d": "D1", "p": 1, "q": f"프로젝트-{i}"}],
            }
            for i in range(1, count + 1)
        ]
    }


# ---------------------------------------------------------------------------
# A. Registry / history
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v10() -> None:
    from app.ai.prompts import profile_extract_v10 as v10
    from app.ai.prompts import profile_extract_v8 as v8
    from app.ai.prompts import profile_extract_v9 as v9
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v10"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v10"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.derive_project_duration is True
    assert cur.clear_catalog_code_customer is True
    assert cur.core_system_prompt == v10.CORE_SYSTEM_PROMPT

    for ver, mod in (
        ("profile-extract-v8", v8),
        ("profile-extract-v9", v9),
    ):
        spec = resolve_profile_prompt(ver)
        assert spec.extraction_mode == "staged"
        assert spec.compact_protocol is True
        assert spec.strict_relation_evidence is False
        assert spec.derive_project_duration is False
        assert spec.core_system_prompt == mod.CORE_SYSTEM_PROMPT


def test_v1_through_v9_prompt_files_unchanged_from_base() -> None:
    for ver in range(1, 10):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(
            base
        ).digest(), path


# ---------------------------------------------------------------------------
# B. Certification acq/exp
# ---------------------------------------------------------------------------


def test_certification_acq_exp_and_legacy_ad_ex() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    # v10 acq -> acquired_date only
    expanded = expand_compact_core(
        {"c": [{"n": "정보처리산업기사", "acq": "2010.12"}]},
        alias_to_id={},
    )
    cert = expanded["certifications"][0]
    assert cert["certification_name"] == "정보처리산업기사"
    assert cert["acquired_date"] == "2010.12"
    assert "expiry_date" not in cert
    doc = normalize_candidate(expanded, catalog={}, allowed_documents={})
    assert doc.certifications[0].acquired_date == "2010.12"
    assert doc.certifications[0].expiry_date is None

    # exp only
    expanded_exp = expand_compact_core(
        {"c": [{"n": "자격", "exp": "2025.12"}]},
        alias_to_id={},
    )
    assert expanded_exp["certifications"][0]["expiry_date"] == "2025.12"
    assert "acquired_date" not in expanded_exp["certifications"][0]

    # legacy ad/ex still accepted
    legacy = expand_compact_core(
        {"c": [{"n": "자격", "ad": "2011.01", "ex": "2020.01"}]},
        alias_to_id={},
    )
    assert legacy["certifications"][0]["acquired_date"] == "2011.01"
    assert legacy["certifications"][0]["expiry_date"] == "2020.01"

    # acq preferred over ad
    prefer = expand_compact_core(
        {"c": [{"n": "자격", "acq": "2010.12", "ad": "1999.01"}]},
        alias_to_id={},
    )
    assert prefer["certifications"][0]["acquired_date"] == "2010.12"


# ---------------------------------------------------------------------------
# C. Customer cust / cu / catalog-code clearing
# ---------------------------------------------------------------------------


def test_customer_cust_cu_and_catalog_code_cleared() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects

    catalog = {
        "BIZ-PUBLIC": ("BIZ", True),
        "CUSTOMER-ENTERPRISE": ("CUSTOMER_TYPE", True),
    }

    # cust literal preserved
    out = expand_compact_projects(
        {"pr": [{"n": "P1", "cust": "현대오토에버", "r": []}]},
        alias_to_id=_ALIAS,
        clear_catalog_code_customer=True,
        catalog=catalog,
    )
    assert out["projects"][0]["customer_name"] == "현대오토에버"

    # legacy cu preserved
    out_cu = expand_compact_projects(
        {"pr": [{"n": "P1", "cu": "기아", "r": []}]},
        alias_to_id=_ALIAS,
        clear_catalog_code_customer=True,
        catalog=catalog,
    )
    assert out_cu["projects"][0]["customer_name"] == "기아"

    # BIZ code as customer cleared under v10 mode; b array remains
    out_biz = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "cust": "BIZ-PUBLIC",
                    "b": ["BIZ-PUBLIC"],
                    "ct": ["CUSTOMER-ENTERPRISE"],
                    "r": [{"d": "D1", "p": 1, "q": "공공"}],
                }
            ]
        },
        alias_to_id=_ALIAS,
        clear_catalog_code_customer=True,
        catalog=catalog,
    )
    proj = out_biz["projects"][0]
    assert "customer_name" not in proj
    assert [x["code"] for x in proj["business_domains"]] == ["BIZ-PUBLIC"]
    assert [x["code"] for x in proj["customer_types"]] == ["CUSTOMER-ENTERPRISE"]

    # Without clear flag (v9 behavior), BIZ-PUBLIC kept as customer_name
    legacy = expand_compact_projects(
        {"pr": [{"n": "P1", "cu": "BIZ-PUBLIC"}]},
        alias_to_id=_ALIAS,
        clear_catalog_code_customer=False,
        catalog=catalog,
    )
    assert legacy["projects"][0]["customer_name"] == "BIZ-PUBLIC"


# ---------------------------------------------------------------------------
# D. Deterministic duration table
# ---------------------------------------------------------------------------


def test_deterministic_duration_month_delta_table() -> None:
    from app.modules.analysis.compact_v8 import derive_duration_months

    cases = [
        ("2023.02", "2024.09", 19),
        ("2022.01", "2023.01", 12),
        ("2019.01", "2021.12", 35),
        ("2018.01", "2018.12", 11),
        ("2016.01", "2017.12", 23),
        ("2015.07", "2015.12", 5),
        ("2012.07", "2015.02", 31),
        ("2023-02", "2024-09", 19),
        ("2019-01", "2021-12", 35),
    ]
    for start, end, expected in cases:
        assert derive_duration_months(start, end) == expected, (start, end)

    assert derive_duration_months("2015.02", "2012.07") is None
    assert derive_duration_months("2015", "2016.01") is None
    assert derive_duration_months("2015.07", None) is None

    from app.modules.analysis.compact_v8 import expand_compact_projects

    derived = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "P",
                    "s": "2019.01",
                    "e": "2021.12",
                    "mo": 29,  # ignored under derive_duration
                    "r": [],
                }
            ]
        },
        alias_to_id={},
        derive_duration=True,
    )
    assert derived["projects"][0]["duration_months"] == 35


# ---------------------------------------------------------------------------
# E. Relation evidence strict vs legacy
# ---------------------------------------------------------------------------


def test_v10_strict_relation_evidence_and_v9_legacy() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    page = (
        "정보보호 강화 구축 사업관리 NT OS "
        "보안솔루션 운영(AD,SEP,NAC 등) PL 운영"
    )
    catalog = {
        "TECH-SEC-AD": ("TECH", True),
        "TECH-SEC-SEP": ("TECH", True),
        "TECH-SEC-NAC": ("TECH", True),
        "JOB-PL": ("JOB", True),
        "EXP-SEC-OPS": ("EXP", True),
    }

    # Strict: no rm => j/t/x dropped
    dropped = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "j": ["JOB-PL"],
                    "x": ["EXP-SEC-OPS"],
                    "r": [{"d": "D1", "p": 1, "q": "P1"}],
                }
            ]
        },
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
    )
    assert "skills" not in dropped["projects"][0]
    assert "jobs" not in dropped["projects"][0]
    assert "expertise" not in dropped["projects"][0]

    # Strict: valid shared rm.t / rm.j / rm.x
    kept = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "운영",
                    "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                    "j": ["JOB-PL"],
                    "x": ["EXP-SEC-OPS"],
                    "r": [{"d": "D1", "p": 2, "q": "보안솔루션 운영"}],
                    "rm": {
                        "t": [
                            {
                                "d": "D1",
                                "p": 2,
                                "q": "보안솔루션 운영(AD,SEP,NAC 등)",
                            }
                        ],
                        "j": [{"d": "D1", "p": 2, "q": "PL 운영"}],
                        "x": [{"d": "D1", "p": 2, "q": "보안솔루션 운영"}],
                    },
                }
            ]
        },
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
    )
    proj = kept["projects"][0]
    assert [s["code"] for s in proj["skills"]] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert proj["skills"][0]["source_refs"][0]["quote_text"] == (
        "보안솔루션 운영(AD,SEP,NAC 등)"
    )
    assert [j["code"] for j in proj["jobs"]] == ["JOB-PL"]
    assert [e["code"] for e in proj["expertise"]] == ["EXP-SEC-OPS"]

    doc = normalize_candidate(
        kept,
        catalog=catalog,
        allowed_documents={_DOC: {2}},
        page_texts={(_DOC, 2): page},
    )
    assert [s.code for s in doc.projects[0].skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]

    # Legacy v9 non-strict: project r falls back for relations
    legacy = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "r": [
                        {
                            "d": "D1",
                            "p": 2,
                            "q": "보안솔루션 운영(AD,SEP,NAC 등)",
                        }
                    ],
                }
            ]
        },
        alias_to_id=_ALIAS,
        strict_relation_evidence=False,
    )
    assert legacy["projects"][0]["skills"][0]["code"] == "TECH-SEC-AD"
    assert legacy["projects"][0]["skills"][0]["source_refs"]


# ---------------------------------------------------------------------------
# F. Cross-project TECH isolation
# ---------------------------------------------------------------------------


def test_cross_project_tech_isolation_under_v10_strict() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    page = (
        "2015 정보보호 강화(HW) 구축 사업관리 NT OS upgrade "
        "현대/기아자동차 정보보안시스템 운영 PL "
        "보안솔루션 운영(AD,SEP,NAC 등)"
    )
    catalog = {
        "TECH-SEC-AD": ("TECH", True),
        "TECH-SEC-SEP": ("TECH", True),
        "TECH-SEC-NAC": ("TECH", True),
        "JOB-PL": ("JOB", True),
        "EXP-SEC-BUILD": ("EXP", True),
        "EXP-SEC-OPS": ("EXP", True),
        "EXP-MGT": ("EXP", True),
    }
    compact = {
        "pr": [
            {
                "n": "정보보호 강화(HW)",
                "s": "2015.07",
                "e": "2015.12",
                "resp": "구축 사업관리",
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "j": ["JOB-PL"],
                "x": ["EXP-SEC-BUILD", "EXP-MGT"],
                "r": [{"d": "D1", "p": 1, "q": "정보보호 강화(HW)"}],
                "rm": {
                    # Only build/mgt evidence — no AD/SEP/NAC quote
                    "x": [
                        {"d": "D1", "p": 1, "q": "구축 사업관리"},
                    ],
                },
            },
            {
                "n": "현대/기아자동차 정보보안시스템 운영",
                "cust": "현대오토에버",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "PL 운영",
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "j": ["JOB-PL"],
                "x": ["EXP-SEC-OPS"],
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "현대/기아자동차 정보보안시스템 운영",
                    }
                ],
                "rm": {
                    "t": [
                        {
                            "d": "D1",
                            "p": 1,
                            "q": "보안솔루션 운영(AD,SEP,NAC 등)",
                        }
                    ],
                    "j": [{"d": "D1", "p": 1, "q": "PL 운영"}],
                    "x": [
                        {
                            "d": "D1",
                            "p": 1,
                            "q": "현대/기아자동차 정보보안시스템 운영",
                        }
                    ],
                },
            },
        ]
    }
    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
    )
    p1, p2 = expanded["projects"]
    assert "skills" not in p1
    assert "jobs" not in p1
    assert {e["code"] for e in p1["expertise"]} == {"EXP-SEC-BUILD", "EXP-MGT"}
    assert p1["duration_months"] == 5

    assert [s["code"] for s in p2["skills"]] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert [j["code"] for j in p2["jobs"]] == ["JOB-PL"]
    assert [e["code"] for e in p2["expertise"]] == ["EXP-SEC-OPS"]
    assert p2["duration_months"] == 31

    doc = normalize_candidate(
        expanded,
        catalog=catalog,
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): page},
    )
    assert doc.projects[0].skills == []
    assert [s.code for s in doc.projects[1].skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]


# ---------------------------------------------------------------------------
# G. Completeness payload
# ---------------------------------------------------------------------------


def test_core_completeness_five_employment_education_cert() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(_v10_core(), alias_to_id={})
    assert len(expanded["employment_history"]) == 5
    assert len(expanded["education"]) == 1
    assert expanded["education"][0]["school_name"] == "서울대"
    assert expanded["certifications"][0]["acquired_date"] == "2010.12"
    assert "EXP-INFRA" in [e["code"] for e in expanded["expertise"]]
    ProfileCandidateDocument.model_validate(expanded)
    doc = normalize_candidate(expanded, catalog={}, allowed_documents={})
    assert len(doc.employment_history) == 5
    assert len(doc.education) == 1
    assert len(doc.certifications) == 1


# ---------------------------------------------------------------------------
# H. Call budget
# ---------------------------------------------------------------------------


def test_v10_happy_path_two_calls(db_session):
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

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v10"
    admin = _create_user(
        db_session, login_id=f"v10_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    _ensure_analysis_code(db_session, "TECH-SEC-AD", "TECH", "Active Directory")
    _ensure_analysis_code(db_session, "EXP-INFRA", "EXP", "Infra")
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT + " AD SEP NAC 시스템 운영",
    )
    run = _queue_run(db_session, person.id, document.id)
    assert run.prompt_version == "profile-extract-v10"

    llm = _StagedSequenceLLM([_v10_core(), _v10_projects(7)])
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert llm.phases == ["core", "projects"]
    assert all(c.get("compact_protocol") == "v10" for c in llm.log_contexts)
    assert "ALL distinct employment" in llm.system_prompts[0]
    assert "Do NOT emit mo" in llm.system_prompts[1] or "mo/duration" in llm.system_prompts[1]
    assert "cust" in llm.system_prompts[1]
    db_session.refresh(run)
    assert len(run.candidate_json["employment_history"]) == 5
    assert len(run.candidate_json["projects"]) == 7
    _cleanup_person(db_session, person.id, admin.id)


def test_v10_recovery_budget_max_three_calls(db_session):
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
        db_session, login_id=f"v10r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(db_session, person.id, document.id)
    llm = _StagedSequenceLLM(
        [
            AIResponseTruncatedError(
                meta={"finish_reason": "length", "total_tokens": 8192}
            ),
            _v10_core(),
            _v10_projects(3),
        ]
    )
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 3
    assert llm.phases == ["core", "core", "projects"]
    _cleanup_person(db_session, person.id, admin.id)


# ---------------------------------------------------------------------------
# Corrective: normalized quote evidence (post-normalize v10 filter)
# ---------------------------------------------------------------------------


def _v10_normalize_projects(
    compact: dict,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed: dict[str, set[int]] | None = None,
    catalog: dict[str, tuple[str, bool]] | None = None,
    strict: bool = True,
):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=strict,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=catalog or {},
    )
    doc = normalize_candidate(
        expanded,
        catalog=catalog or {},
        allowed_documents=allowed if allowed is not None else {_DOC: {1, 2}},
        page_texts=page_texts,
    )
    if strict:
        doc = apply_normalized_quote_evidence(doc)
    return doc


def test_v10_project_without_r_dropped_after_normalize() -> None:
    """A: project with no r => dropped under v10 post-normalize filter."""
    doc = _v10_normalize_projects(
        {"pr": [{"n": "프로젝트-무증거", "s": "2019.01", "e": "2021.12"}]},
        page_texts={(_DOC, 1): "프로젝트-무증거"},
    )
    assert doc.projects == []


def test_v10_project_invalid_ref_after_normalize_dropped() -> None:
    """B: invalid alias/page/quote after normalize => project dropped."""
    # Unknown alias expands to no refs.
    doc_alias = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "r": [{"d": "D9", "p": 1, "q": "P1"}],
                }
            ]
        },
        page_texts={(_DOC, 1): "P1 원문"},
    )
    assert doc_alias.projects == []

    # Invalid page discarded by normalize.
    doc_page = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "r": [{"d": "D1", "p": 99, "q": "P1"}],
                }
            ]
        },
        page_texts={(_DOC, 1): "P1 원문"},
        allowed={_DOC: {1}},
    )
    assert doc_page.projects == []

    # Quote not in page_texts => quote_text cleared to None => not sufficient.
    doc_quote = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "r": [{"d": "D1", "p": 1, "q": "없는인용문"}],
                }
            ]
        },
        page_texts={(_DOC, 1): "다른 내용만 있음"},
    )
    assert doc_quote.projects == []
    # Sanity: normalize alone would keep a quote-less SourceRef.
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        {"pr": [{"n": "P1", "r": [{"d": "D1", "p": 1, "q": "없는인용문"}]}]},
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
    )
    raw = normalize_candidate(
        expanded,
        catalog={},
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): "다른 내용만 있음"},
    )
    assert len(raw.projects) == 1
    assert raw.projects[0].source_refs
    assert raw.projects[0].source_refs[0].quote_text is None


def test_v10_project_valid_r_retained() -> None:
    """C: valid project r with quote in page_texts => retained."""
    doc = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "현대보안운영",
                    "r": [{"d": "D1", "p": 1, "q": "현대보안운영"}],
                }
            ]
        },
        page_texts={(_DOC, 1): "현대보안운영 PL AD"},
    )
    assert len(doc.projects) == 1
    assert doc.projects[0].project_name == "현대보안운영"
    assert doc.projects[0].source_refs[0].quote_text == "현대보안운영"


def test_v10_relation_invalid_page_dropped() -> None:
    """D: j/t/x rm with invalid page => relation dropped; project may remain."""
    catalog = {"TECH-SEC-AD": ("TECH", True), "JOB-PL": ("JOB", True)}
    doc = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "j": ["JOB-PL"],
                    "r": [{"d": "D1", "p": 1, "q": "P1"}],
                    "rm": {
                        "t": [{"d": "D1", "p": 99, "q": "AD"}],
                        "j": [{"d": "D1", "p": 99, "q": "PL"}],
                    },
                }
            ]
        },
        page_texts={(_DOC, 1): "P1 AD PL"},
        allowed={_DOC: {1}},
        catalog=catalog,
    )
    assert len(doc.projects) == 1
    assert doc.projects[0].skills == []
    assert doc.projects[0].jobs == []


def test_v10_relation_quote_missing_from_page_dropped() -> None:
    """E: rm quote not in page_texts => quote cleared => relation dropped."""
    catalog = {"TECH-SEC-AD": ("TECH", True)}
    doc = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "r": [{"d": "D1", "p": 1, "q": "P1"}],
                    "rm": {
                        "t": [{"d": "D1", "p": 1, "q": "없는TECH인용"}],
                    },
                }
            ]
        },
        page_texts={(_DOC, 1): "P1 만 있음"},
        catalog=catalog,
    )
    assert len(doc.projects) == 1
    assert doc.projects[0].skills == []


def test_v10_relation_valid_normalized_rm_retained() -> None:
    """F: valid normalized rm quote => relation retained."""
    catalog = {
        "TECH-SEC-AD": ("TECH", True),
        "JOB-PL": ("JOB", True),
        "EXP-SEC-OPS": ("EXP", True),
    }
    page = "P1 보안솔루션 운영(AD,SEP,NAC 등) PL 운영"
    doc = _v10_normalize_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "j": ["JOB-PL"],
                    "x": ["EXP-SEC-OPS"],
                    "r": [{"d": "D1", "p": 1, "q": "P1"}],
                    "rm": {
                        "t": [
                            {
                                "d": "D1",
                                "p": 1,
                                "q": "보안솔루션 운영(AD,SEP,NAC 등)",
                            }
                        ],
                        "j": [{"d": "D1", "p": 1, "q": "PL 운영"}],
                        "x": [{"d": "D1", "p": 1, "q": "보안솔루션 운영"}],
                    },
                }
            ]
        },
        page_texts={(_DOC, 1): page},
        catalog=catalog,
    )
    assert len(doc.projects) == 1
    assert [s.code for s in doc.projects[0].skills] == ["TECH-SEC-AD"]
    assert doc.projects[0].skills[0].source_refs[0].quote_text
    assert [j.code for j in doc.projects[0].jobs] == ["JOB-PL"]
    assert [e.code for e in doc.projects[0].expertise] == ["EXP-SEC-OPS"]


def test_v9_legacy_keeps_project_without_quote_filter() -> None:
    """G: v9 non-strict path does not apply post-normalize quote filter."""
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    # No r — v9 still keeps the project after normalize.
    expanded = expand_compact_projects(
        {"pr": [{"n": "레거시프로젝트", "mo": 12}]},
        alias_to_id=_ALIAS,
        strict_relation_evidence=False,
    )
    doc = normalize_candidate(
        expanded,
        catalog={},
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): "irrelevant"},
    )
    assert len(doc.projects) == 1
    assert doc.projects[0].project_name == "레거시프로젝트"
    assert doc.projects[0].source_refs == []

    # Quote cleared to None — v9 keeps project and relation without filter.
    expanded2 = expand_compact_projects(
        {
            "pr": [
                {
                    "n": "P1",
                    "t": ["TECH-SEC-AD"],
                    "r": [{"d": "D1", "p": 1, "q": "없는인용"}],
                }
            ]
        },
        alias_to_id=_ALIAS,
        strict_relation_evidence=False,
    )
    doc2 = normalize_candidate(
        expanded2,
        catalog={"TECH-SEC-AD": ("TECH", True)},
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): "다른내용"},
    )
    assert len(doc2.projects) == 1
    assert doc2.projects[0].source_refs[0].quote_text is None
    assert len(doc2.projects[0].skills) == 1
    assert doc2.projects[0].skills[0].source_refs[0].quote_text is None
