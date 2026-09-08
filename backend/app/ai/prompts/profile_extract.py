"""Profile extraction prompt registry — resolve by AnalysisRun.prompt_version."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.ai.prompts import profile_extract_v1, profile_extract_v2
from app.ai.providers.errors import AIProviderError

CURRENT_PROFILE_PROMPT_VERSION = "profile-extract-v2"


class UnknownProfilePromptVersionError(AIProviderError):
    """Stored AnalysisRun.prompt_version is missing or unsupported."""


@dataclass(frozen=True)
class ProfilePromptSpec:
    prompt_version: str
    schema_version: str
    system_prompt: str
    build_user_prompt: Callable[..., str]


def _spec_from_module(module) -> ProfilePromptSpec:
    return ProfilePromptSpec(
        prompt_version=module.PROMPT_VERSION,
        schema_version=module.SCHEMA_VERSION,
        system_prompt=module.SYSTEM_PROMPT,
        build_user_prompt=module.build_user_prompt,
    )


_REGISTRY: dict[str, ProfilePromptSpec] = {
    profile_extract_v1.PROMPT_VERSION: _spec_from_module(profile_extract_v1),
    profile_extract_v2.PROMPT_VERSION: _spec_from_module(profile_extract_v2),
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
