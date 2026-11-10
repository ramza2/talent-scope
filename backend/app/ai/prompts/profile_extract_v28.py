"""Opt-in PROJECT date correction experiment against profile-extract-v27.

Reuses v27's PROJECTS prompt (period-before-name row binding guidance) and
enables unique owned-period date correction in PROJECTS normalize before
chunk merge. CORE prompt and every other pipeline flag stay on the v25
baseline via the registry. This version is intentionally not the default.
"""

from __future__ import annotations

from app.ai.prompts import profile_extract_v27 as _v27

PROMPT_VERSION = "profile-extract-v28"

# v28-only: overwrite already-filled wrong dates when name+customer+period
# ownership is uniquely proven from root source refs (fail-closed otherwise).
CORRECT_PROJECT_DATES_FROM_UNIQUE_OWNED_PERIOD = True

PROJECTS_SYSTEM_PROMPT = _v27.PROJECTS_SYSTEM_PROMPT


def build_projects_user_prompt(
    *,
    code_catalog: str,
    document_blocks: str,
    recovery_retry: bool = False,
) -> str:
    """Reuse v27/v26 input and catalog boundaries."""
    return _v27.build_projects_user_prompt(
        code_catalog=code_catalog,
        document_blocks=document_blocks,
        recovery_retry=recovery_retry,
    )
