"""Profile extraction prompt registry — resolve by AnalysisRun.prompt_version."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.ai.prompts import (
    profile_extract_v1,
    profile_extract_v2,
    profile_extract_v3,
    profile_extract_v4,
    profile_extract_v5,
    profile_extract_v6,
    profile_extract_v7,
    profile_extract_v8,
    profile_extract_v9,
    profile_extract_v10,
    profile_extract_v11,
    profile_extract_v12,
    profile_extract_v13,
)
from app.ai.providers.errors import AIProviderError

CURRENT_PROFILE_PROMPT_VERSION = "profile-extract-v13"

ExtractionMode = Literal["single", "staged"]


class UnknownProfilePromptVersionError(AIProviderError):
    """Stored AnalysisRun.prompt_version is missing or unsupported."""


@dataclass(frozen=True)
class ProfilePromptSpec:
    prompt_version: str
    schema_version: str
    system_prompt: str
    build_user_prompt: Callable[..., str]
    extraction_mode: ExtractionMode = "single"
    core_system_prompt: str | None = None
    projects_system_prompt: str | None = None
    build_core_user_prompt: Callable[..., str] | None = None
    build_projects_user_prompt: Callable[..., str] | None = None
    compact_protocol: bool = False
    strict_relation_evidence: bool = False
    derive_project_duration: bool = False
    clear_catalog_code_customer: bool = False
    promote_exact_catalog_codes: bool = False
    backfill_exact_core_evidence: bool = False
    backfill_exact_project_evidence: bool = False


def _spec_from_module(module) -> ProfilePromptSpec:
    return ProfilePromptSpec(
        prompt_version=module.PROMPT_VERSION,
        schema_version=module.SCHEMA_VERSION,
        system_prompt=module.SYSTEM_PROMPT,
        build_user_prompt=module.build_user_prompt,
        extraction_mode="single",
    )


def _spec_from_staged_module(module) -> ProfilePromptSpec:
    return ProfilePromptSpec(
        prompt_version=module.PROMPT_VERSION,
        schema_version=module.SCHEMA_VERSION,
        system_prompt=module.CORE_SYSTEM_PROMPT,
        build_user_prompt=module.build_core_user_prompt,
        extraction_mode="staged",
        core_system_prompt=module.CORE_SYSTEM_PROMPT,
        projects_system_prompt=module.PROJECTS_SYSTEM_PROMPT,
        build_core_user_prompt=module.build_core_user_prompt,
        build_projects_user_prompt=module.build_projects_user_prompt,
        compact_protocol=bool(getattr(module, "COMPACT_PROTOCOL", False)),
        strict_relation_evidence=bool(
            getattr(module, "STRICT_RELATION_EVIDENCE", False)
        ),
        derive_project_duration=bool(
            getattr(module, "DERIVE_PROJECT_DURATION", False)
        ),
        clear_catalog_code_customer=bool(
            getattr(module, "CLEAR_CATALOG_CODE_CUSTOMER", False)
        ),
        promote_exact_catalog_codes=bool(
            getattr(module, "PROMOTE_EXACT_CATALOG_CODES", False)
        ),
        backfill_exact_core_evidence=bool(
            getattr(module, "BACKFILL_EXACT_CORE_EVIDENCE", False)
        ),
        backfill_exact_project_evidence=bool(
            getattr(module, "BACKFILL_EXACT_PROJECT_EVIDENCE", False)
        ),
    )


_REGISTRY: dict[str, ProfilePromptSpec] = {
    profile_extract_v1.PROMPT_VERSION: _spec_from_module(profile_extract_v1),
    profile_extract_v2.PROMPT_VERSION: _spec_from_module(profile_extract_v2),
    profile_extract_v3.PROMPT_VERSION: _spec_from_module(profile_extract_v3),
    profile_extract_v4.PROMPT_VERSION: _spec_from_module(profile_extract_v4),
    profile_extract_v5.PROMPT_VERSION: _spec_from_module(profile_extract_v5),
    profile_extract_v6.PROMPT_VERSION: _spec_from_module(profile_extract_v6),
    profile_extract_v7.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v7),
    profile_extract_v8.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v8),
    profile_extract_v9.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v9),
    profile_extract_v10.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v10),
    profile_extract_v11.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v11),
    profile_extract_v12.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v12),
    profile_extract_v13.PROMPT_VERSION: _spec_from_staged_module(profile_extract_v13),
}


def resolve_profile_prompt(prompt_version: str | None) -> ProfilePromptSpec:
    """Resolve exact prompt for a stored AnalysisRun.prompt_version.

    Never silently falls back to the latest prompt.
    """
    if prompt_version is None or not str(prompt_version).strip():
        raise UnknownProfilePromptVersionError("prompt_version is missing")
    key = str(prompt_version).strip()
    spec = _REGISTRY.get(key)
    if spec is None:
        raise UnknownProfilePromptVersionError(
            f"unsupported prompt_version: {key}"
        )
    return spec


def current_profile_prompt() -> ProfilePromptSpec:
    return resolve_profile_prompt(CURRENT_PROFILE_PROMPT_VERSION)
