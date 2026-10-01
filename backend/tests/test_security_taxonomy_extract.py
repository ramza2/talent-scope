"""TECH/EXP security classification — normalize + profile-extract-v6 policy."""

from __future__ import annotations

from app.ai.prompts.profile_extract_v6 import SYSTEM_PROMPT
from app.modules.analysis.normalize import normalize_candidate

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALLOWED = {_DOC: {1}}

_CATALOG = {
    "TECH-SEC-AD": ("TECH", True),
    "TECH-SEC-NAC": ("TECH", True),
    "TECH-SEC-SEP": ("TECH", True),
    "TECH-LANG-PYTHON": ("TECH", True),
    "EXP-SEC": ("EXP", True),
    "EXP-SEC-OPS": ("EXP", True),
    "EXP-SEC-BUILD": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "EXP-MGT": ("EXP", True),
    "EXP-AI-RAG": ("EXP", True),
}


def _base_raw(**overrides: object) -> dict:
    raw: dict = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "테스트"},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    raw.update(overrides)
    return raw


def test_normalize_security_operations_candidate_codes() -> None:
    raw = _base_raw(
        skills=[
            {"raw_value": "AD", "code": "TECH-SEC-AD"},
            {"raw_value": "NAC", "code": "TECH-SEC-NAC"},
            {"raw_value": "SEP", "code": "TECH-SEC-SEP"},
        ],
        expertise=[
            {
                "raw_value": "정보보안 운영",
                "code": "EXP-SEC-OPS",
                "evidence_type": "EXPLICIT",
            },
            {
                "raw_value": "정보시스템 운영",
                "code": "EXP-INFRA",
                "evidence_type": "EXPLICIT",
            },
            {
                "raw_value": "사업관리",
                "code": "EXP-MGT",
                "evidence_type": "INFERRED",
            },
        ],
    )
    doc = normalize_candidate(raw, catalog=_CATALOG, allowed_documents=_ALLOWED)
    assert [s.code for s in doc.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-NAC",
        "TECH-SEC-SEP",
    ]
    by_exp = {e.raw_value: e.code for e in doc.expertise}
    assert by_exp["정보보안 운영"] == "EXP-SEC-OPS"
    assert by_exp["정보시스템 운영"] == "EXP-INFRA"
    assert by_exp["사업관리"] == "EXP-MGT"


def test_prompt_work_phrases_are_not_tech() -> None:
    # v6 keeps a compact activity list rather than repeating every phrase.
    assert "시스템 운영/구축/유지보수/기술지원/백업/이관/사업관리" in SYSTEM_PROMPT
    assert "NOT TECH" in SYSTEM_PROMPT
    assert "TECH = explicit concrete technology" in SYSTEM_PROMPT


def test_normalize_explicit_security_tech_only() -> None:
    raw = _base_raw(
        skills=[
            {"raw_value": "AD", "code": "TECH-SEC-AD"},
            {"raw_value": "NAC", "code": "TECH-SEC-NAC"},
            {"raw_value": "SEP", "code": "TECH-SEC-SEP"},
            # EXP inside skills must be rejected by TECH-only validation.
            {"raw_value": "정보보안 운영", "code": "EXP-SEC-OPS"},
        ]
    )
    doc = normalize_candidate(raw, catalog=_CATALOG, allowed_documents=_ALLOWED)
    assert [s.code for s in doc.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-NAC",
        "TECH-SEC-SEP",
        None,
    ]


def test_normalize_security_expertise_codes() -> None:
    raw = _base_raw(
        expertise=[
            {"raw_value": "정보보안", "code": "EXP-SEC", "evidence_type": "EXPLICIT"},
            {
                "raw_value": "정보보안 운영",
                "code": "EXP-SEC-OPS",
                "evidence_type": "EXPLICIT",
            },
            {
                "raw_value": "정보보안 구축",
                "code": "EXP-SEC-BUILD",
                "evidence_type": "EXPLICIT",
            },
            # TECH inside expertise must not survive as EXP.
            {"raw_value": "AD", "code": "TECH-SEC-AD", "evidence_type": "EXPLICIT"},
        ]
    )
    doc = normalize_candidate(raw, catalog=_CATALOG, allowed_documents=_ALLOWED)
    by_raw = {e.raw_value: e.code for e in doc.expertise}
    assert by_raw["정보보안"] == "EXP-SEC"
    assert by_raw["정보보안 운영"] == "EXP-SEC-OPS"
    assert by_raw["정보보안 구축"] == "EXP-SEC-BUILD"
    assert by_raw["AD"] is None


def test_normalize_infra_management_root_expertise() -> None:
    raw = _base_raw(
        expertise=[
            {
                "raw_value": "시스템 운영",
                "code": "EXP-INFRA",
                "evidence_type": "EXPLICIT",
            },
            {
                "raw_value": "사업관리",
                "code": "EXP-MGT",
                "evidence_type": "EXPLICIT",
            },
        ],
        skills=[],
    )
    doc = normalize_candidate(raw, catalog=_CATALOG, allowed_documents=_ALLOWED)
    assert doc.skills == []
    assert [e.code for e in doc.expertise] == ["EXP-INFRA", "EXP-MGT"]


def test_prompt_no_invented_security_tech_from_ops_alone() -> None:
    assert "정보보안 운영 alone" in SYSTEM_PROMPT
    assert "AD/NAC/SEP" in SYSTEM_PROMPT
    assert "explicitly present in source" in SYSTEM_PROMPT


def test_prompt_no_fake_skill_duration_from_career() -> None:
    assert "experience_months" in SYSTEM_PROMPT
    assert "last_used_year" in SYSTEM_PROMPT
    assert "technology-specific" in SYSTEM_PROMPT
    assert "total career" in SYSTEM_PROMPT
    assert "unrelated projects" in SYSTEM_PROMPT


def test_prompt_security_exp_child_guidance() -> None:
    assert "EXP-SEC" in SYSTEM_PROMPT
    assert "EXP-SEC-OPS" in SYSTEM_PROMPT
    assert "EXP-SEC-BUILD" in SYSTEM_PROMPT
    assert "EXP-INFRA" in SYSTEM_PROMPT
    assert "EXP-MGT" in SYSTEM_PROMPT
