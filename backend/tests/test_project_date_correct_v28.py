"""v28: fail-closed correction of filled wrong project dates via unique ownership."""

from __future__ import annotations

from dataclasses import fields

from app.ai.prompts import profile_extract_v25 as v25
from app.ai.prompts import profile_extract_v27 as v27
from app.ai.prompts import profile_extract_v28 as v28
from app.ai.prompts.profile_extract import (
    CURRENT_PROFILE_PROMPT_VERSION,
    ProfilePromptSpec,
    current_profile_prompt,
    resolve_profile_prompt,
)
from app.ai.schemas.profile_candidate import (
    AnalysisMetaCandidate,
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
    SummaryCandidate,
)
from app.modules.analysis.compact_v8 import (
    correct_project_dates_from_unique_owned_period,
)

D1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _project(
    name="데이터 플랫폼 구축",
    customer="고객A",
    start="2006.01",
    end="2006.06",
    *,
    quote="PL",
    doc=D1,
    page=1,
):
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        duration_months=6,
        source_refs=(
            [SourceRef(document_id=doc, page_no=page, quote_text=quote)]
            if quote
            else []
        ),
    )


def _candidate(*projects):
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스터"),
        projects=list(projects),
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _correct(doc, pages):
    allowed: dict[str, set[int]] = {}
    for doc_id, page_no in pages:
        allowed.setdefault(doc_id, set()).add(page_no)
    return correct_project_dates_from_unique_owned_period(
        doc, page_texts=pages, allowed_documents=allowed
    )


def test_v28_is_opt_in_and_isolates_correction_flag() -> None:
    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v25"
    assert current_profile_prompt().prompt_version == "profile-extract-v25"

    baseline = resolve_profile_prompt("profile-extract-v25")
    prior = resolve_profile_prompt("profile-extract-v27")
    experiment = resolve_profile_prompt("profile-extract-v28")

    assert experiment.prompt_version == "profile-extract-v28"
    assert experiment.correct_project_dates_from_unique_owned_period is True
    assert v28.CORRECT_PROJECT_DATES_FROM_UNIQUE_OWNED_PERIOD is True
    assert baseline.correct_project_dates_from_unique_owned_period is False
    assert prior.correct_project_dates_from_unique_owned_period is False
    assert experiment.projects_system_prompt == v27.PROJECTS_SYSTEM_PROMPT
    assert experiment.build_projects_user_prompt is v28.build_projects_user_prompt

    changed = {
        "prompt_version",
        "projects_system_prompt",
        "build_projects_user_prompt",
        "correct_project_dates_from_unique_owned_period",
    }
    for field in fields(ProfilePromptSpec):
        if field.name not in changed:
            assert getattr(experiment, field.name) == getattr(baseline, field.name)
    assert not hasattr(v25, "CORRECT_PROJECT_DATES_FROM_UNIQUE_OWNED_PERIOD")
    assert not hasattr(v27, "CORRECT_PROJECT_DATES_FROM_UNIQUE_OWNED_PERIOD")


def test_period_before_name_corrects_adjacent_row_shift() -> None:
    # 수행기간 → 사업구분 → 프로젝트명 → 역할 → 회사/직급 → 고객사
    page = (
        "2004.04 ~ 2005.12 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A\n"
        "2006.01 ~ 2006.06 | SI | 보안 시스템 개선 | PM | 회사 | 고객B"
    )
    # LLM attached the next row's period to the previous project.
    wrong = _project(start="2006.01", end="2006.06", quote="PL")
    neighbor = _project(
        name="보안 시스템 개선",
        customer="고객B",
        start="2006.01",
        end="2006.06",
        quote="PM",
    )
    out = _correct(_candidate(wrong, neighbor), {(D1, 1): page}).projects
    assert (out[0].start_date, out[0].end_date, out[0].duration_months) == (
        "2004.04",
        "2005.12",
        20,
    )
    assert (out[1].start_date, out[1].end_date) == ("2006.01", "2006.06")
    assert out[0].source_refs == wrong.source_refs


def test_period_after_name_also_corrects_unique_owned_mismatch() -> None:
    page = (
        "데이터 플랫폼 구축 | 2004.04 ~ 2005.12 | DB 설계 담당 | 고객A\n"
        "보안 시스템 개선 | 2006.01 ~ 2006.06 | 보안 운영 담당 | 고객B"
    )
    wrong = _project(
        start="2006.01", end="2006.06", quote="DB 설계 담당"
    )
    result = _correct(_candidate(wrong), {(D1, 1): page}).projects[0]
    assert (result.start_date, result.end_date, result.duration_months) == (
        "2004.04",
        "2005.12",
        20,
    )


def test_adjacent_rows_keep_distinct_owned_periods() -> None:
    page = (
        "2004.04 ~ 2005.12 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A\n"
        "2006.01 ~ 2006.06 | SI | 보안 시스템 개선 | PM | 회사 | 고객B"
    )
    a = _project(start="2006.01", end="2006.06", quote="PL")
    b = _project(
        name="보안 시스템 개선",
        customer="고객B",
        start="2004.04",
        end="2005.12",
        quote="PM",
    )
    out = _correct(_candidate(a, b), {(D1, 1): page}).projects
    assert [(p.start_date, p.end_date) for p in out] == [
        ("2004.04", "2005.12"),
        ("2006.01", "2006.06"),
    ]


def test_duplicate_identity_or_conflicting_periods_fail_closed() -> None:
    page = "2004.04 ~ 2005.12 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A"
    a = _project(start="2006.01", end="2006.06", quote="PL")
    duplicate = _project(start="2006.01", end="2006.06", quote="PL")
    ambiguous = _correct(_candidate(a, duplicate), {(D1, 1): page})
    assert [(p.start_date, p.end_date) for p in ambiguous.projects] == [
        ("2006.01", "2006.06"),
        ("2006.01", "2006.06"),
    ]

    conflict_page = (
        "2004.04 ~ 2005.12 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A\n"
        "2003.01 ~ 2003.06 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A"
    )
    source = _project(start="2006.01", end="2006.06", quote="PL")
    assert (
        _correct(_candidate(source), {(D1, 1): conflict_page}).projects[0]
        == source
    )


def test_already_correct_or_weak_identity_unchanged() -> None:
    page = "2004.04 ~ 2005.12 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A"
    correct = _project(start="2004.04", end="2005.12", quote="PL")
    assert _correct(_candidate(correct), {(D1, 1): page}).projects[0] == correct

    no_customer = _project(customer=None, quote="PL")
    assert (
        _correct(_candidate(no_customer), {(D1, 1): page}).projects[0]
        == no_customer
    )

    no_quote = _project(quote=None)
    assert _correct(_candidate(no_quote), {(D1, 1): page}).projects[0] == no_quote
