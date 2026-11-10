"""Opt-in flexible project-period parse against profile-extract-v28.

Reuses v28's PROJECTS prompt and unique owned-period date correction, and
enables day-precision / omitted-end-year / newline-tolerant period parsing
normalized to YYYY.MM. CORE and every other pipeline flag stay on the v25
baseline via the registry. This version is intentionally not the default.
"""

from __future__ import annotations

from app.ai.prompts import profile_extract_v28 as _v28

PROMPT_VERSION = "profile-extract-v29"

CORRECT_PROJECT_DATES_FROM_UNIQUE_OWNED_PERIOD = True
# v29-only: parse YYYY.M.D~M.D / YYYY.M.D~YYYY.M.D (and newlines) to YYYY.MM.
FLEXIBLE_PROJECT_PERIOD_PARSE = True

PROJECTS_SYSTEM_PROMPT = _v28.PROJECTS_SYSTEM_PROMPT


def build_projects_user_prompt(
    *,
    code_catalog: str,
    document_blocks: str,
    recovery_retry: bool = False,
) -> str:
    """Reuse v28/v27/v26 input and catalog boundaries."""
    return _v28.build_projects_user_prompt(
        code_catalog=code_catalog,
        document_blocks=document_blocks,
        recovery_retry=recovery_retry,
    )
