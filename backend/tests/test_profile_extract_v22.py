"""profile-extract-v22: v21 CORE + enabled PROJECT JOB/EXP supplement."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


def test_profile_extract_registry_current_is_v22() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts import profile_extract_v21 as v21
    from app.ai.prompts import profile_extract_v22 as v22
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v22"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v22"

    # v21 CORE flags remain enabled.
    assert cur.backfill_semantic_core_job_evidence is True
    assert cur.require_semantic_core_job_evidence is True
    assert cur.supplement_semantic_core_exp_from_source is True
    assert v22.BACKFILL_SEMANTIC_CORE_JOB_EVIDENCE is True
    assert v22.REQUIRE_SEMANTIC_CORE_JOB_EVIDENCE is True
    assert v22.SUPPLEMENT_SEMANTIC_CORE_EXP_FROM_SOURCE is True

    # v22 enables PROJECT relation supplement (+ enhanced row semantics).
    assert cur.supplement_semantic_project_relations_from_source is True
    assert v22.SUPPLEMENT_SEMANTIC_PROJECT_RELATIONS_FROM_SOURCE is True

    # PROJECT prompt/schema contract unchanged from v20/v21.
    assert v22.PROJECTS_SYSTEM_PROMPT == v20.PROJECTS_SYSTEM_PROMPT
    assert v22.PROJECTS_SCHEMA_GUIDE == v20.PROJECTS_SCHEMA_GUIDE
    assert v22.PROJECTS_SYSTEM_PROMPT == v21.PROJECTS_SYSTEM_PROMPT
    assert v22.CORE_SYSTEM_PROMPT == v21.CORE_SYSTEM_PROMPT
    assert v22.CORE_SCHEMA_GUIDE == v21.CORE_SCHEMA_GUIDE

    v20_spec = resolve_profile_prompt("profile-extract-v20")
    v21_spec = resolve_profile_prompt("profile-extract-v21")
    assert v20_spec.supplement_semantic_project_relations_from_source is False
    assert v21_spec.supplement_semantic_project_relations_from_source is False
    assert v20_spec.backfill_semantic_core_job_evidence is False
    assert v21_spec.backfill_semantic_core_job_evidence is True


def test_v22_activates_enhanced_project_semantics_wiring() -> None:
    """Flag True must drive enhanced_project_relation_semantics in service path."""
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v20 = resolve_profile_prompt("profile-extract-v20")
    v21 = resolve_profile_prompt("profile-extract-v21")
    v22 = resolve_profile_prompt("profile-extract-v22")
    assert v20.supplement_semantic_project_relations_from_source is False
    assert v21.supplement_semantic_project_relations_from_source is False
    assert v22.supplement_semantic_project_relations_from_source is True
    # Companion PROJECT backfill/guard flags stay on for all three.
    for spec in (v20, v21, v22):
        assert spec.backfill_semantic_project_exp_evidence is True
        assert spec.backfill_semantic_project_job_evidence is True
        assert spec.require_semantic_project_exp_evidence is True
        assert spec.require_semantic_project_job_evidence is True
