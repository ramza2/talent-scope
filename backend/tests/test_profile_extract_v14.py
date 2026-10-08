"""profile-extract-v14 CORE root skill (TECH) recall."""

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

_BASE_SHA = "033187fca35b48f3150a12b879b10e523677dff4"
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC1}

_CATALOG: dict[str, tuple[str, bool]] = {
    "TECH-SEC-AD": ("TECH", True),
    "TECH-SEC-SEP": ("TECH", True),
    "TECH-SEC-NAC": ("TECH", True),
    "EXP-MGT": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "EXP-SEC-OPS": ("EXP", True),
}


def test_profile_extract_registry_v14_remains_resolvable() -> None:
    """v14 stays resolvable unchanged; current may advance (v15+)."""
    from app.ai.prompts import profile_extract_v13 as v13
    from app.ai.prompts import profile_extract_v14 as v14
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v14_spec = resolve_profile_prompt("profile-extract-v14")
    assert v14_spec.prompt_version == "profile-extract-v14"
    assert v14_spec.extraction_mode == "staged"
    assert v14_spec.compact_protocol is True
    assert v14_spec.strict_relation_evidence is True
    assert v14_spec.derive_project_duration is True
    assert v14_spec.clear_catalog_code_customer is True
    assert v14_spec.promote_exact_catalog_codes is True
    assert v14_spec.backfill_exact_core_evidence is True
    assert v14_spec.backfill_exact_project_evidence is True
    assert v14_spec.core_system_prompt == v14.CORE_SYSTEM_PROMPT
    assert v14.BACKFILL_EXACT_CORE_EVIDENCE is True
    assert v14.BACKFILL_EXACT_PROJECT_EVIDENCE is True

    v13_spec = resolve_profile_prompt("profile-extract-v13")
    assert v13_spec.backfill_exact_core_evidence is True
    assert v13_spec.backfill_exact_project_evidence is True
    assert v13_spec.promote_exact_catalog_codes is True
    assert v13_spec.core_system_prompt == v13.CORE_SYSTEM_PROMPT
    assert v13_spec.core_system_prompt != v14.CORE_SYSTEM_PROMPT


def test_v1_through_v13_prompt_files_byte_identical_to_base() -> None:
    for ver in range(1, 14):
        path = f"backend/app/ai/prompts/profile_extract_v{ver}.py"
        current = Path("/workspace") / path
        base = subprocess.check_output(
            ["git", "show", f"{_BASE_SHA}:{path}"],
            cwd="/workspace",
        )
        assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(
            base
        ).digest(), path


def test_v14_core_prompt_explicit_tech_to_root_s_contract() -> None:
    from app.ai.prompts import profile_extract_v14 as v14

    core = v14.CORE_SYSTEM_PROMPT
    user = v14.build_core_user_prompt(
        code_catalog="TECH-SEC-AD",
        document_blocks="AD SEP NAC",
    )
    assert "Root s[]" in core
    assert "WHOLE" in core and "document" in core
    assert "technology/product/tool" in core.lower()
    assert "Deduplicate" in core or "deduplicate" in core.lower()
    assert "RAG" in core and "LLM" in core
    assert "explicit TECH" in user or "skills(s[])" in user
    # PROJECTS unchanged vs v13
    from app.ai.prompts import profile_extract_v13 as v13

    assert v14.PROJECTS_SYSTEM_PROMPT == v13.PROJECTS_SYSTEM_PROMPT
    assert v14.build_projects_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v13.build_projects_user_prompt(code_catalog="x", document_blocks="y")


def test_compact_s_ad_sep_nac_yields_three_root_skills() -> None:
    from app.modules.analysis.compact_v8 import (
        expand_compact_core,
        promote_exact_root_catalog_codes,
    )
    from app.modules.analysis.normalize import normalize_candidate

    compact = {
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
    }
    expanded = expand_compact_core(compact, alias_to_id=_ALIAS)
    doc = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents={_DOC1: {1}},
        page_texts={_DOC1: {1: "AD SEP NAC"}},
    )
    doc = promote_exact_root_catalog_codes(doc, catalog=_CATALOG)
    assert len(doc.skills) == 3
    assert [s.code for s in doc.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert len(doc.expertise) == 3


def test_staged_merge_keeps_core_skills_without_project_t_promotion() -> None:
    from app.modules.analysis.compact_v8 import (
        expand_compact_core,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    core_compact = {
        "s": [
            {"v": "AD", "c": "TECH-SEC-AD"},
            {"v": "SEP", "c": "TECH-SEC-SEP"},
            {"v": "NAC", "c": "TECH-SEC-NAC"},
        ],
    }
    projects_compact = {
        "pr": [
            {
                "n": "보안솔루션운영",
                "cust": "고객기관",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "PL 운영",
                "t": ["TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"],
                "r": [{"d": "D1", "p": 1, "q": "보안솔루션운영"}],
                "rm": {
                    "t": [{"d": "D1", "p": 1, "q": "보안솔루션운영"}],
                },
            }
        ]
    }
    page_texts = {_DOC1: {1: "보안솔루션운영 AD SEP NAC"}}
    allowed = {_DOC1: {1}}
    core = normalize_candidate(
        expand_compact_core(core_compact, alias_to_id=_ALIAS),
        catalog=_CATALOG,
        allowed_documents=allowed,
        page_texts=page_texts,
    )
    projects = normalize_candidate(
        expand_compact_projects(
            projects_compact,
            alias_to_id=_ALIAS,
            strict_relation_evidence=True,
            derive_duration=True,
            clear_catalog_code_customer=True,
            catalog=_CATALOG,
        ),
        catalog=_CATALOG,
        allowed_documents=allowed,
        page_texts=page_texts,
    )
    merged = core.model_copy(update={"projects": list(projects.projects)})
    assert len(merged.skills) == 3
    assert [s.code for s in merged.skills] == [
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    ]
    assert len(merged.projects) == 1
    assert len(merged.projects[0].skills) == 3

    # Empty CORE skills + project t must NOT promote into root skills.
    empty_core = normalize_candidate(
        expand_compact_core({"x": [{"v": "사업관리", "c": "EXP-MGT"}]}, alias_to_id=_ALIAS),
        catalog=_CATALOG,
        allowed_documents=allowed,
        page_texts=page_texts,
    )
    no_promo = empty_core.model_copy(update={"projects": list(projects.projects)})
    assert len(no_promo.skills) == 0
    assert len(no_promo.projects[0].skills) == 3


def test_exp_only_compact_yields_zero_root_skills() -> None:
    from app.modules.analysis.compact_v8 import expand_compact_core
    from app.modules.analysis.normalize import normalize_candidate

    compact = {
        "x": [
            {"v": "사업관리", "c": "EXP-MGT"},
            {"v": "정보시스템 운영", "c": "EXP-INFRA"},
            {"v": "보안 운영", "c": "EXP-SEC-OPS"},
        ],
    }
    doc = normalize_candidate(
        expand_compact_core(compact, alias_to_id=_ALIAS),
        catalog=_CATALOG,
        allowed_documents={_DOC1: {1}},
        page_texts={_DOC1: {1: "사업관리 정보시스템 운영 보안 운영"}},
    )
    assert len(doc.skills) == 0
    assert len(doc.expertise) == 3
