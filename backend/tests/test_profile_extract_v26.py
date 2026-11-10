"""v26 is an opt-in, projects-only prompt simplification experiment."""

from __future__ import annotations

from dataclasses import fields

from app.ai.prompts import profile_extract_v25 as v25
from app.ai.prompts import profile_extract_v26 as v26
from app.ai.prompts.profile_extract import (
    CURRENT_PROFILE_PROMPT_VERSION,
    ProfilePromptSpec,
    current_profile_prompt,
    resolve_profile_prompt,
)


def test_v26_experiment_does_not_change_default_or_v25_pipeline() -> None:
    baseline = resolve_profile_prompt("profile-extract-v25")
    experiment = resolve_profile_prompt("profile-extract-v26")

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v25"
    assert current_profile_prompt() is baseline
    assert experiment.extraction_mode == "staged"
    assert experiment.projects_system_prompt == v26.PROJECTS_SYSTEM_PROMPT
    assert experiment.build_projects_user_prompt is v26.build_projects_user_prompt

    # A/B must vary prompt text only, never post-processing/safety switches.
    changed = {
        "prompt_version",
        "projects_system_prompt",
        "build_projects_user_prompt",
    }
    for field in fields(ProfilePromptSpec):
        if field.name not in changed:
            assert getattr(experiment, field.name) == getattr(baseline, field.name)


def test_v26_project_instructions_are_shorter_but_keep_essential_contract() -> None:
    prompt = v26.PROJECTS_SYSTEM_PROMPT
    assert len(prompt) < len(v25.PROJECTS_SYSTEM_PROMPT) * 0.75
    for required in (
        "프로젝트명이 아닙니다",
        "PM/PL/TA/SA/AA/PMO",
        "인접 행",
        "YYYY.MM",
        "JOB-MGT-PM",
        "JOB-MGT-PL",
        "원문 인용",
        'Root MUST be exactly {"pr":[...]}',
        "j/t/x",
        "certificate_no",
    ):
        assert required in prompt


def test_v26_project_user_prompt_keeps_catalog_and_source_boundaries() -> None:
    kwargs = {"code_catalog": "JOB-MGT-PM", "document_blocks": "사업명 | 기간 | 역할"}
    simple = v26.build_projects_user_prompt(**kwargs)
    baseline = v25.build_projects_user_prompt(**kwargs)
    assert len(simple) < len(baseline)
    assert "===== BEGIN CODE CATALOG =====" in simple
    assert "===== END CODE CATALOG =====" in simple
    assert "===== BEGIN UNTRUSTED DOCUMENT DATA =====" in simple
    assert "===== END UNTRUSTED DOCUMENT DATA =====" in simple
    assert '최상위 구조는 {"pr":[...]}' in simple
    assert "[RECOVERY]" not in simple

    recovery = v26.build_projects_user_prompt(**kwargs, recovery_retry=True)
    assert recovery.startswith("[RECOVERY]")
    assert recovery.endswith("===== END UNTRUSTED DOCUMENT DATA =====\n")
