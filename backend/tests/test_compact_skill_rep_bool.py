"""Fail-soft guards for compact skill representative flag (rep)."""

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
    "TECH-SEC-SSO": ("TECH", True),
}


def test_skill_rep_true_false_preserved() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core

    expanded = expand_compact_core(
        {
            "s": [
                {"v": "AD", "c": "TECH-SEC-AD", "rep": True},
                {"v": "SEP", "c": "TECH-SEC-SEP", "rep": False},
            ]
        },
        alias_to_id=_ALIAS,
    )
    assert expanded["skills"][0]["is_representative"] is True
    assert expanded["skills"][1]["is_representative"] is False
    doc = ProfileCandidateDocument.model_validate(expanded)
    assert doc.skills[0].is_representative is True
    assert doc.skills[1].is_representative is False


def test_skill_rep_malformed_string_omitted_validation_succeeds() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core

    expanded = expand_compact_core(
        {
            "s": [
                {
                    "v": "AD",
                    "c": "TECH-SEC-AD",
                    "rep": "보안솔루션 운영",
                    "r": [{"d": "D1", "p": 1, "q": "AD"}],
                }
            ]
        },
        alias_to_id=_ALIAS,
    )
    skill = expanded["skills"][0]
    assert "is_representative" not in skill
    assert skill["raw_value"] == "AD"
    assert skill["code"] == "TECH-SEC-AD"
    assert skill["source_refs"]
    doc = ProfileCandidateDocument.model_validate(expanded)
    assert len(doc.skills) == 1
    assert doc.skills[0].is_representative is False


def test_skill_rep_malformed_non_bool_omitted() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core

    cases = [
        {"x": 1},
        1,
        0,
        1.0,
        ["yes"],
        None,
        "시스템 운영/구축",
        "true",
        "false",
    ]
    for rep in cases:
        expanded = expand_compact_core(
            {"s": [{"v": "AD", "c": "TECH-SEC-AD", "rep": rep}]},
            alias_to_id=_ALIAS,
        )
        skill = expanded["skills"][0]
        assert "is_representative" not in skill
        assert skill["code"] == "TECH-SEC-AD"
        doc = ProfileCandidateDocument.model_validate(expanded)
        assert doc.skills[0].is_representative is False


def test_production_malformed_skill_rep_strings_multi_skills_validate() -> None:
    """Production shape: free-text rep must not fail CORE validation."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    page = "보안솔루션 운영(AD,SEP,NAC 등) 시스템 운영/구축 AD SEP NAC SSO"
    compact = {
        "s": [
            {
                "v": "AD",
                "c": "TECH-SEC-AD",
                "rep": "보안솔루션 운영",
                "r": [{"d": "D1", "p": 1, "q": "AD"}],
            },
            {
                "v": "SEP",
                "c": "TECH-SEC-SEP",
                "rep": "시스템 운영/구축",
                "r": [{"d": "D1", "p": 1, "q": "SEP"}],
            },
            {
                "v": "NAC",
                "c": "TECH-SEC-NAC",
                "rep": True,
                "r": [{"d": "D1", "p": 1, "q": "NAC"}],
            },
            {
                "v": "SSO",
                "c": "TECH-SEC-SSO",
                "rep": {"x": 1},
                "r": [{"d": "D1", "p": 1, "q": "SSO"}],
            },
        ]
    }
    expanded = expand_compact_core(compact, alias_to_id=_ALIAS)
    assert len(expanded["skills"]) == 4
    assert "is_representative" not in expanded["skills"][0]
    assert "is_representative" not in expanded["skills"][1]
    assert expanded["skills"][2]["is_representative"] is True
    assert "is_representative" not in expanded["skills"][3]

    doc = ProfileCandidateDocument.model_validate(expanded)
    assert len(doc.skills) == 4
    assert [s.is_representative for s in doc.skills] == [False, False, True, False]

    normalized = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): page},
    )
    assert len(normalized.skills) == 4
    assert [s.code for s in normalized.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
        "TECH-SEC-SSO",
    ]
    assert [s.is_representative for s in normalized.skills] == [
        False,
        False,
        True,
        False,
    ]
