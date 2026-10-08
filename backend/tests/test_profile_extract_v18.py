"""profile-extract-v18 inline PROJECTS j/t/x relation evidence."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_BASE_SHA = "b2e2a6fac249912f1efd2f43772fddb371456d2e"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC1}
_CATALOG: dict[str, tuple[str, bool]] = {
    "JOB-MGT": ("JOB", True),
    "JOB-MGT-PM": ("JOB", True),
    "JOB-MGT-PL": ("JOB", True),
    "JOB-ARC-TA": ("JOB", True),
    "JOB-OPS-SYS": ("JOB", True),
    "TECH-SEC-AD": ("TECH", True),
    "EXP-MGT": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "BIZ-PUBLIC": ("BIZ", True),
    "BIZ-DEFENSE": ("BIZ", True),
    "BIZ-MEDIA": ("BIZ", True),
}


def _normalize_projects(
    compact: dict,
    *,
    page_texts: dict[tuple[str, int], str],
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
        catalog=_CATALOG,
    )
    doc = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents={_DOC1: {1}},
        page_texts=page_texts,
    )
    if strict:
        doc = apply_normalized_quote_evidence(doc)
    return doc, expanded


# ---------------------------------------------------------------------------
# Registry / immutability / CORE parity
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v18() -> None:
    from app.ai.prompts import profile_extract_v17 as v17
    from app.ai.prompts import profile_extract_v18 as v18
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v18"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v18"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.validate_projects_recovery_root is True
    assert cur.validate_core_structured_completeness is True
    assert cur.projects_system_prompt == v18.PROJECTS_SYSTEM_PROMPT
    assert cur.core_system_prompt == v18.CORE_SYSTEM_PROMPT

    v17_spec = resolve_profile_prompt("profile-extract-v17")
    assert v17_spec.prompt_version == "profile-extract-v17"
    assert v17_spec.projects_system_prompt == v17.PROJECTS_SYSTEM_PROMPT
    assert v17_spec.core_system_prompt == v17.CORE_SYSTEM_PROMPT
    assert v17_spec.strict_relation_evidence is True


def test_v17_prompt_file_byte_identical_to_base() -> None:
    path = "backend/app/ai/prompts/profile_extract_v17.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_BASE_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


def test_v18_core_parity_and_inline_projects_contract() -> None:
    from app.ai.prompts import profile_extract_v17 as v17
    from app.ai.prompts import profile_extract_v18 as v18

    assert v18.CORE_SYSTEM_PROMPT == v17.CORE_SYSTEM_PROMPT
    assert v18.CORE_SCHEMA_GUIDE == v17.CORE_SCHEMA_GUIDE
    assert v18.CORE_RECOVERY_RETRY_INSTRUCTION == v17.CORE_RECOVERY_RETRY_INSTRUCTION
    assert v18.build_core_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v17.build_core_user_prompt(code_catalog="x", document_blocks="y")
    assert v18.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    ) == v17.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )

    blob = (
        v18.PROJECTS_SYSTEM_PROMPT
        + v18.PROJECTS_SCHEMA_GUIDE
        + v18.build_projects_user_prompt(code_catalog="x", document_blocks="y")
        + v18.build_projects_user_prompt(
            code_catalog="x", document_blocks="y", recovery_retry=True
        )
    )
    assert '"c":"CATALOG-CODE"' in blob or '{"c":CODE' in blob or '"c":CODE' in blob
    assert "inline" in blob.lower() or "MUST be objects" in blob or "MUST be {" in blob
    assert "no separate rm" in blob.lower() or "Do NOT emit separate rm" in blob
    assert "rm REQUIRED" not in blob
    assert "same-project rm" not in blob.lower()
    assert "JOB-ARC-TA" in blob
    assert "JOB-MGT" in blob and "EXP-MGT" in blob
    assert "JOB-OPS-SYS" in blob and "EXP-INFRA" in blob
    assert "BIZ-PUBLIC" in blob and "BIZ-DEFENSE" in blob and "BIZ-MEDIA" in blob
    assert "CUSTOMER_TYPE" in blob
    assert v18.PROJECTS_SYSTEM_PROMPT != v17.PROJECTS_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Inline relation pipeline (existing runtime; no compact_v8 changes)
# ---------------------------------------------------------------------------


def test_v18_inline_relations_survive_strict_pipeline() -> None:
    page = (
        "공공기관 시스템구축 및 운영 > IT시스템기술지원 TA 사업관리 "
        "국방 영화 프로젝트"
    )
    compact = {
        "pr": [
            {
                "n": "공공기관 시스템구축 및 운영",
                "cust": "공공기관",
                "s": "2020-01",
                "e": "2021-12",
                "resp": "TA 사업관리 IT시스템기술지원",
                "j": [
                    {
                        "c": "JOB-ARC-TA",
                        "r": [{"d": "D1", "p": 1, "q": "TA"}],
                    },
                    {
                        "c": "JOB-MGT",
                        "r": [{"d": "D1", "p": 1, "q": "사업관리"}],
                    },
                    {
                        "c": "JOB-OPS-SYS",
                        "r": [
                            {
                                "d": "D1",
                                "p": 1,
                                "q": "시스템구축 및 운영 > IT시스템기술지원",
                            }
                        ],
                    },
                ],
                "x": [
                    {
                        "c": "EXP-MGT",
                        "r": [{"d": "D1", "p": 1, "q": "사업관리"}],
                    },
                    {
                        "c": "EXP-INFRA",
                        "r": [
                            {
                                "d": "D1",
                                "p": 1,
                                "q": "시스템구축 및 운영 > IT시스템기술지원",
                            }
                        ],
                    },
                ],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "공공기관 시스템구축 및 운영"}],
            }
        ]
    }
    doc, expanded = _normalize_projects(compact, page_texts={(_DOC1, 1): page})
    assert len(expanded["projects"][0]["jobs"]) == 3
    assert len(doc.projects) == 1
    proj = doc.projects[0]
    assert {j.code for j in proj.jobs} == {"JOB-ARC-TA", "JOB-MGT", "JOB-OPS-SYS"}
    assert {e.code for e in proj.expertise} == {"EXP-MGT", "EXP-INFRA"}
    assert [b.code for b in proj.business_domains] == ["BIZ-PUBLIC"]
    assert proj.customer_types == []
    assert all((ref.quote_text or "").strip() for j in proj.jobs for ref in j.source_refs)


def test_v18_inline_without_r_still_drops() -> None:
    page = "공공기관 시스템구축 및 운영 TA 사업관리"
    compact = {
        "pr": [
            {
                "n": "공공기관 시스템구축 및 운영",
                "cust": "공공기관",
                "j": [{"c": "JOB-ARC-TA"}, {"c": "JOB-MGT"}],
                "x": [{"c": "EXP-MGT"}],
                "t": [{"c": "TECH-SEC-AD"}],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "공공기관 시스템구축 및 운영"}],
            }
        ]
    }
    doc, expanded = _normalize_projects(compact, page_texts={(_DOC1, 1): page})
    assert "jobs" not in expanded["projects"][0]
    assert "expertise" not in expanded["projects"][0]
    assert "skills" not in expanded["projects"][0]
    assert len(doc.projects) == 1
    assert doc.projects[0].jobs == []
    assert doc.projects[0].expertise == []
    assert doc.projects[0].skills == []
    assert [b.code for b in doc.projects[0].business_domains] == ["BIZ-PUBLIC"]


def test_legacy_rm_parser_still_supported_unchanged() -> None:
    """Runtime still accepts v17-style string codes + rm (no compact_v8 change)."""
    page = "프로젝트-L TA 사업관리"
    compact = {
        "pr": [
            {
                "n": "프로젝트-L",
                "cust": "고객",
                "j": ["JOB-ARC-TA", "JOB-MGT"],
                "x": ["EXP-MGT"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트-L"}],
                "rm": {
                    "j": [{"d": "D1", "p": 1, "q": "TA 사업관리"}],
                    "x": [{"d": "D1", "p": 1, "q": "사업관리"}],
                },
            }
        ]
    }
    doc, expanded = _normalize_projects(compact, page_texts={(_DOC1, 1): page})
    assert {j["code"] for j in expanded["projects"][0]["jobs"]} == {
        "JOB-ARC-TA",
        "JOB-MGT",
    }
    assert {j.code for j in doc.projects[0].jobs} == {"JOB-ARC-TA", "JOB-MGT"}
    assert [e.code for e in doc.projects[0].expertise] == ["EXP-MGT"]


def test_v18_biz_unchanged_and_no_customer_type_invention() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_projects

    page = "국방부 체계 영화진흥위원회 사업"
    compact = {
        "pr": [
            {
                "n": "국방 체계",
                "cust": "국방부",
                "b": ["BIZ-DEFENSE"],
                "r": [{"d": "D1", "p": 1, "q": "국방부"}],
            },
            {
                "n": "영화진흥 사업",
                "cust": "영화진흥위원회",
                "b": ["BIZ-MEDIA"],
                "r": [{"d": "D1", "p": 1, "q": "영화진흥위원회"}],
            },
        ]
    }
    doc, expanded = _normalize_projects(compact, page_texts={(_DOC1, 1): page})
    assert [p.business_domains[0].code for p in doc.projects] == [
        "BIZ-DEFENSE",
        "BIZ-MEDIA",
    ]
    assert all(p.customer_types == [] for p in doc.projects)
    assert all("customer_types" not in p for p in expanded["projects"])
