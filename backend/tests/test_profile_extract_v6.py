"""profile-extract-v6 registry, compactness, and TECH/EXP policy guards."""

from __future__ import annotations

from app.ai.prompts import profile_extract_v5 as v5
from app.ai.prompts import profile_extract_v6 as v6
from app.ai.prompts.profile_extract import (
    CURRENT_PROFILE_PROMPT_VERSION,
    current_profile_prompt,
    resolve_profile_prompt,
)

# Bloated #54 in-place v5 file was ~6463 bytes; keep v6 SYSTEM_PROMPT well below.
_V6_SYSTEM_PROMPT_MAX_CHARS = 4200
_V6_RECOVERY_MAX_CHARS = 220


def test_profile_extract_registry_current_is_v6() -> None:
    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v6"
    assert current_profile_prompt().prompt_version == "profile-extract-v6"
    assert resolve_profile_prompt("profile-extract-v5").prompt_version == (
        "profile-extract-v5"
    )
    assert resolve_profile_prompt("profile-extract-v6").prompt_version == (
        "profile-extract-v6"
    )
    assert (
        resolve_profile_prompt("profile-extract-v5").system_prompt
        == v5.SYSTEM_PROMPT
    )
    assert (
        resolve_profile_prompt("profile-extract-v6").system_prompt
        == v6.SYSTEM_PROMPT
    )


def test_profile_extract_v5_immutable_without_security_policy() -> None:
    """Historical v5 must not carry #54 Security-specific additions."""
    assert v5.PROMPT_VERSION == "profile-extract-v5"
    assert "EXP-SEC" not in v5.SYSTEM_PROMPT
    assert "EXP-SEC-OPS" not in v5.SYSTEM_PROMPT
    assert "EXP-SEC-BUILD" not in v5.SYSTEM_PROMPT
    assert "AD/NAC/SEP" not in v5.SYSTEM_PROMPT
    assert "Output budget" not in v5.SYSTEM_PROMPT
    assert "strongest source_ref" not in v5.SYSTEM_PROMPT


def test_profile_extract_v6_semantic_coverage() -> None:
    prompt = v6.SYSTEM_PROMPT
    assert "NOT TECH" in prompt
    assert "EXP-INFRA" in prompt
    assert "EXP-MGT" in prompt
    assert "EXP-SEC" in prompt
    assert "EXP-SEC-OPS" in prompt
    assert "EXP-SEC-BUILD" in prompt
    assert "AD/NAC/SEP" in prompt
    assert "explicitly present in source" in prompt
    assert "정보보안 운영 alone" in prompt
    assert "experience_months" in prompt
    assert "last_used_year" in prompt
    assert "technology-specific" in prompt
    assert "total career" in prompt
    assert "unrelated projects" in prompt
    assert "RAG/LLM/AI Agent" in prompt
    assert "Never invent a catalog code" in prompt


def test_profile_extract_v6_compact_output_policy() -> None:
    prompt = v6.SYSTEM_PROMPT
    assert "strongest source_ref" in prompt
    assert "No duplicate facts" in prompt or "no duplicate facts" in prompt.lower()
    assert "concise" in prompt
    assert "Complete compact root JSON" in prompt
    assert "never drop documented projects/entities" in prompt


def test_profile_extract_v6_recovery_and_system_size_guards() -> None:
    assert v6.RECOVERY_RETRY_INSTRUCTION.startswith("[RECOVERY]")
    assert "COMPLETE compact" in v6.RECOVERY_RETRY_INSTRUCTION
    assert "no duplicate facts" in v6.RECOVERY_RETRY_INSTRUCTION
    assert len(v6.RECOVERY_RETRY_INSTRUCTION) <= _V6_RECOVERY_MAX_CHARS
    assert len(v6.SYSTEM_PROMPT) <= _V6_SYSTEM_PROMPT_MAX_CHARS
    # Materially below the bloated #54 v5 SYSTEM_PROMPT (~5.4k chars).
    assert len(v6.SYSTEM_PROMPT) < len(v5.SYSTEM_PROMPT) + 900
    assert len(v6.SYSTEM_PROMPT) < 5400

    first = v6.build_user_prompt(
        code_catalog="C", document_blocks="D", recovery_retry=False
    )
    second = v6.build_user_prompt(
        code_catalog="C", document_blocks="D", recovery_retry=True
    )
    assert "[RECOVERY]" not in first
    assert second.startswith(v6.RECOVERY_RETRY_INSTRUCTION)
    assert len(second) - len(first) <= len(v6.RECOVERY_RETRY_INSTRUCTION) + 2
