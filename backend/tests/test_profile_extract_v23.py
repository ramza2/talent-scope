"""profile-extract-v23: v22 + PROJECT TECH leaf supplement flag."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


def test_profile_extract_registry_current_is_v25() -> None:
    from app.ai.prompts import profile_extract_v22 as v22
    from app.ai.prompts import profile_extract_v23 as v23
    from app.ai.prompts import profile_extract_v24 as v24
    from app.ai.prompts import profile_extract_v25 as v25
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v25"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v25"
    assert cur.guard_core_section_authority is True
    assert cur.supplement_literal_project_tech_from_source is True
    assert v23.SUPPLEMENT_LITERAL_PROJECT_TECH_FROM_SOURCE is True
    assert v24.SUPPLEMENT_LITERAL_PROJECT_TECH_FROM_SOURCE is True
    # Inherits v22 PROJECT JOB/EXP supplement.
    assert cur.supplement_semantic_project_relations_from_source is True
    assert cur.backfill_semantic_core_job_evidence is True
    # Prompt contract unchanged from v22.
    assert v23.PROJECTS_SYSTEM_PROMPT == v22.PROJECTS_SYSTEM_PROMPT
    assert v23.CORE_SYSTEM_PROMPT == v22.CORE_SYSTEM_PROMPT
    # v24 changes only project table-row guidance; core stays compatible.
    assert v24.PROJECTS_SYSTEM_PROMPT != v23.PROJECTS_SYSTEM_PROMPT
    assert v24.CORE_SYSTEM_PROMPT == v23.CORE_SYSTEM_PROMPT

    # v25 strengthens section authority without dropping v24 compatibility.
    assert v25.CORE_SYSTEM_PROMPT != v24.CORE_SYSTEM_PROMPT
    assert v25.PROJECTS_SYSTEM_PROMPT != v24.PROJECTS_SYSTEM_PROMPT
    assert resolve_profile_prompt("profile-extract-v24").guard_core_section_authority is False
    v22_spec = resolve_profile_prompt("profile-extract-v22")
    assert v22_spec.supplement_literal_project_tech_from_source is False
