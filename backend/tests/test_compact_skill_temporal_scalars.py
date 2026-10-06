"""Fail-soft guards for compact skill temporal scalars (y / m)."""

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
_CATALOG = {
    "TECH-SEC-AD": ("TECH", True),
    "TECH-SEC-SEP": ("TECH", True),
    "TECH-SEC-NAC": ("TECH", True),
}


def test_production_malformed_skill_ym_date_strings_omitted() -> None:
    """Production shape: y/m as YYYY.MM must not fail CORE validation."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    page = "보안솔루션 운영(AD,SEP,NAC 등) AD SEP NAC"
    compact = {
        "s": [
            {
                "v": "AD",
                "c": "TECH-SEC-AD",
                "y": "2012.07",
                "m": "2015.03",
                "r": [{"d": "D1", "p": 1, "q": "AD"}],
            },
            {
                "v": "SEP",
                "c": "TECH-SEC-SEP",
                "y": "2012.07",
                "m": "2015.03",
                "r": [{"d": "D1", "p": 1, "q": "SEP"}],
            },
            {
                "v": "NAC",
                "c": "TECH-SEC-NAC",
                "y": "2012.07",
                "m": "2015.03",
                "r": [{"d": "D1", "p": 1, "q": "NAC"}],
            },
        ]
    }
    expanded = expand_compact_core(compact, alias_to_id=_ALIAS)
    assert len(expanded["skills"]) == 3
    for skill in expanded["skills"]:
        assert "last_used_year" not in skill
        assert "experience_months" not in skill
        assert skill["code"].startswith("TECH-SEC-")
        assert skill["source_refs"]

    doc = ProfileCandidateDocument.model_validate(expanded)
    assert len(doc.skills) == 3
    assert all(s.last_used_year is None for s in doc.skills)
    assert all(s.experience_months is None for s in doc.skills)

    normalized = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): page},
    )
    assert len(normalized.skills) == 3
    assert [s.code for s in normalized.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert all(s.last_used_year is None for s in normalized.skills)
    assert all(s.experience_months is None for s in normalized.skills)
    assert all(s.source_refs and s.source_refs[0].quote_text for s in normalized.skills)


def test_valid_numeric_skill_ym_accepted_as_int() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    quote = "AD 운영 2012.07~2015.03 (2년 8개월)"
    page = quote
    for y, m in ((2015, 32), ("2015", "32")):
        expanded = expand_compact_core(
            {
                "s": [
                    {
                        "v": "AD",
                        "c": "TECH-SEC-AD",
                        "y": y,
                        "m": m,
                        "r": [{"d": "D1", "p": 1, "q": quote}],
                    }
                ]
            },
            alias_to_id=_ALIAS,
        )
        assert expanded["skills"][0]["last_used_year"] == 2015
        assert expanded["skills"][0]["experience_months"] == 32
        ProfileCandidateDocument.model_validate(expanded)
        doc = normalize_candidate(
            expanded,
            catalog=_CATALOG,
            allowed_documents={_DOC: {1}},
            page_texts={(_DOC, 1): page},
        )
        assert doc.skills[0].last_used_year == 2015
        assert doc.skills[0].experience_months == 32
        assert doc.skills[0].code == "TECH-SEC-AD"


def test_malformed_optional_skill_ym_omitted_without_dropping_skill() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    cases = [
        {"y": True, "m": False},
        {"y": 2015.0, "m": 32.5},
        {"y": [2015], "m": {"m": 32}},
        {"y": "2012.07", "m": "2015-03"},
        {"y": "D1", "m": "about 3 years"},
    ]
    for ym in cases:
        expanded = expand_compact_core(
            {
                "s": [
                    {
                        "v": "AD",
                        "c": "TECH-SEC-AD",
                        **ym,
                        "r": [{"d": "D1", "p": 1, "q": "AD"}],
                    }
                ]
            },
            alias_to_id=_ALIAS,
        )
        skill = expanded["skills"][0]
        assert skill["raw_value"] == "AD"
        assert skill["code"] == "TECH-SEC-AD"
        assert "last_used_year" not in skill
        assert "experience_months" not in skill
        assert skill["source_refs"]
        doc = ProfileCandidateDocument.model_validate(expanded)
        assert len(doc.skills) == 1
        normalized = normalize_candidate(
            expanded,
            catalog=_CATALOG,
            allowed_documents={_DOC: {1}},
            page_texts={(_DOC, 1): "AD"},
        )
        assert len(normalized.skills) == 1
        assert normalized.skills[0].code == "TECH-SEC-AD"
        assert normalized.skills[0].last_used_year is None
        assert normalized.skills[0].experience_months is None
