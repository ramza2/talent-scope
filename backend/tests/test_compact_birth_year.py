"""Fail-soft guards for compact profile birth_year (p.by)."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC}


def test_optional_birth_year_accepted_forms() -> None:
    from app.modules.analysis.compact_v8 import _optional_birth_year

    assert _optional_birth_year(1976) == 1976
    assert _optional_birth_year("1976") == 1976
    assert _optional_birth_year("1976년 04월 05일") == 1976
    assert _optional_birth_year("1976-04-05") == 1976
    assert _optional_birth_year("1976.04.05") == 1976
    assert _optional_birth_year("1976/04/05") == 1976
    assert _optional_birth_year("1976년") == 1976
    assert _optional_birth_year(" 1976년 4월 5일 ") == 1976


def test_optional_birth_year_rejected_forms() -> None:
    from app.modules.analysis.compact_v8 import _optional_birth_year

    assert _optional_birth_year(True) is None
    assert _optional_birth_year(False) is None
    assert _optional_birth_year(1976.0) is None
    assert _optional_birth_year([]) is None
    assert _optional_birth_year({}) is None
    assert _optional_birth_year("76") is None
    assert _optional_birth_year("1976abc") is None
    assert _optional_birth_year("생년 1976") is None
    assert _optional_birth_year("about 1976") is None
    assert _optional_birth_year("") is None
    assert _optional_birth_year(None) is None


def test_expand_profile_production_birth_date_string() -> None:
    """Exact production shape: by='1976년 04월 05일' must not ValidationError."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_core(
        {
            "p": {
                "n": "홍길동",
                "by": "1976년 04월 05일",
                "ph": "010-1234-5678",
            },
            "j": [{"v": "PL", "c": "JOB-MGT-PL"}],
            "w": [{"co": "알파소프트", "s": "2005.03", "e": "2008.02"}],
            "conf": 0.9,
        },
        alias_to_id=_ALIAS,
    )
    assert expanded["profile"]["birth_year"] == 1976
    assert expanded["profile"]["name"] == "홍길동"
    assert expanded["profile"]["phone"] == "010-1234-5678"

    doc = ProfileCandidateDocument.model_validate(expanded)
    assert doc.profile.birth_year == 1976

    normalized = normalize_candidate(
        expanded,
        catalog={"JOB-MGT-PL": ("JOB", True)},
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): "홍길동 1976년 04월 05일 010-1234-5678 PL 알파소프트"},
    )
    assert normalized.profile.birth_year == 1976
    assert normalized.profile.name == "홍길동"
    assert len(normalized.jobs) == 1
    assert len(normalized.employment_history) == 1


def test_expand_profile_malformed_birth_year_omitted_rest_survives() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    for bad in (True, 1976.0, [], {}, "76", "1976abc", "생년 1976"):
        expanded = expand_compact_core(
            {
                "p": {"n": "홍길동", "by": bad, "em": "a@b.com"},
                "j": [{"v": "PM", "c": "JOB-MGT-PM"}],
            },
            alias_to_id={},
        )
        assert "birth_year" not in expanded["profile"]
        assert expanded["profile"]["name"] == "홍길동"
        assert expanded["profile"]["email"] == "a@b.com"
        doc = ProfileCandidateDocument.model_validate(expanded)
        assert doc.profile.birth_year is None
        normalized = normalize_candidate(
            expanded, catalog={"JOB-MGT-PM": ("JOB", True)}, allowed_documents={}
        )
        assert normalized.profile.birth_year is None
        assert normalized.profile.name == "홍길동"
        assert len(normalized.jobs) == 1


def test_birth_year_malformed_does_not_block_v13_core_backfill() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_exact_core_evidence,
        expand_compact_core,
        promote_exact_root_catalog_codes,
    )
    from app.modules.analysis.normalize import normalize_candidate

    page = "홍길동 PL 알파소프트 정보시스템 운영"
    expanded = expand_compact_core(
        {
            "p": {"n": "홍길동", "by": "1976년 04월 05일", "ti": "PL"},
            "j": [{"v": "PL", "c": "JOB-MGT-PL"}],
            "x": [{"v": "정보시스템 운영", "c": "EXP-INFRA"}],
            "w": [{"co": "알파소프트"}],
        },
        alias_to_id=_ALIAS,
    )
    assert expanded["profile"]["birth_year"] == 1976
    doc = normalize_candidate(
        expanded,
        catalog={
            "JOB-MGT-PL": ("JOB", True),
            "EXP-INFRA": ("EXP", True),
        },
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): page},
    )
    doc = promote_exact_root_catalog_codes(
        doc,
        catalog={
            "JOB-MGT-PL": ("JOB", True),
            "EXP-INFRA": ("EXP", True),
        },
    )
    doc = backfill_exact_core_evidence(
        doc,
        page_texts={(_DOC, 1): page},
        allowed_documents={_DOC: {1}},
    )
    assert doc.profile.birth_year == 1976
    assert doc.jobs[0].source_refs[0].quote_text == "PL"
    assert doc.expertise[0].source_refs[0].quote_text == "정보시스템 운영"
    assert doc.employment_history[0].source_refs[0].quote_text == "알파소프트"
