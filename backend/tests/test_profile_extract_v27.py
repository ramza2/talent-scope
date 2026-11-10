"""Opt-in profile-extract-v27: period-first table row binding prompt only."""

from __future__ import annotations

from dataclasses import fields

from app.ai.prompts import profile_extract as registry
from app.ai.prompts import profile_extract_v26 as v26
from app.ai.prompts import profile_extract_v27 as v27
from app.ai.prompts.profile_extract import ProfilePromptSpec


def test_v27_is_registered_but_not_current_default() -> None:
    assert registry.CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v25"
    assert registry.current_profile_prompt().prompt_version == "profile-extract-v25"
    assert "profile-extract-v27" in registry._REGISTRY


def test_v27_reuses_v25_core_and_pipeline_flags() -> None:
    spec = registry.resolve_profile_prompt("profile-extract-v27")
    baseline = registry.resolve_profile_prompt("profile-extract-v25")

    assert spec.prompt_version == "profile-extract-v27"
    assert spec.extraction_mode == baseline.extraction_mode == "staged"
    assert spec.projects_system_prompt == v27.PROJECTS_SYSTEM_PROMPT
    assert spec.projects_system_prompt != baseline.projects_system_prompt
    assert spec.projects_system_prompt != v26.PROJECTS_SYSTEM_PROMPT
    assert spec.build_projects_user_prompt is v27.build_projects_user_prompt

    changed = {
        "prompt_version",
        "projects_system_prompt",
        "build_projects_user_prompt",
    }
    for field in fields(ProfilePromptSpec):
        if field.name not in changed:
            assert getattr(spec, field.name) == getattr(baseline, field.name)


def test_v27_projects_prompt_requires_period_before_name_same_row_binding() -> None:
    prompt = v27.PROJECTS_SYSTEM_PROMPT
    assert "수행기간 → 사업구분 → 프로젝트명 → 역할 → 회사/직급 → 고객사" in prompt
    assert "같은 행의 프로젝트명에 속합니다" in prompt
    assert "바로 다음 행의 프로젝트에 붙이" in prompt
    assert "아래 행의" in prompt and "위 행 기간" in prompt
    assert "기간이 프로젝트명보다 앞에 나오는 표" in prompt


def test_v27_projects_user_prompt_reuses_v26_boundaries() -> None:
    kwargs = {"code_catalog": "JOB-DEV", "document_blocks": "DOC"}
    prompt = v27.build_projects_user_prompt(**kwargs)
    assert prompt == v26.build_projects_user_prompt(**kwargs)
    assert "===== BEGIN CODE CATALOG =====" in prompt
    assert "===== END CODE CATALOG =====" in prompt
    assert "===== BEGIN UNTRUSTED DOCUMENT DATA =====" in prompt
    assert "===== END UNTRUSTED DOCUMENT DATA =====" in prompt
    assert "JOB-DEV" in prompt
    assert "DOC" in prompt
    assert '최상위 구조는 {"pr":[...]}' in prompt
    assert "[RECOVERY]" not in prompt


def test_v27_recovery_retry_user_prompt_matches_v26() -> None:
    kwargs = {"code_catalog": "JOB-DEV", "document_blocks": "DOC"}
    prompt = v27.build_projects_user_prompt(**kwargs, recovery_retry=True)
    assert prompt == v26.build_projects_user_prompt(**kwargs, recovery_retry=True)
    assert prompt.startswith("[RECOVERY]")
    assert prompt.endswith("===== END UNTRUSTED DOCUMENT DATA =====\n")
