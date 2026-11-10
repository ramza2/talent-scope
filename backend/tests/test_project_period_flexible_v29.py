"""v29: flexible day/omitted-year/newline period parse + owned-period correction."""

from __future__ import annotations

from dataclasses import fields

from app.ai.prompts import profile_extract_v28 as v28
from app.ai.prompts import profile_extract_v29 as v29
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
    _PROJECT_FULL_RANGE_RE,
    correct_project_dates_from_unique_owned_period,
    parse_project_date_range_from_texts,
)

D1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

# Anonymized reproductions of the 8 LIVE source period forms that legacy
# FULL_RANGE missed (3) or DATE_RANGE mis-parsed (8). Expected YYYY.MM.
V29_REAL_PERIOD_FORMATS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("2023.4.1~6.30", ("2023.04", "2023.06")),
    ("2022.1.3~3.31", ("2022.01", "2022.03")),
    ("2020.5.1~\n9.30", ("2020.05", "2020.09")),
    ("2019.7.1~2020.1.31", ("2019.07", "2020.01")),
    ("2018.6.11~2019.1.10", ("2018.06", "2019.01")),
    ("2019.07.01 ~ 2020.01.31", ("2019.07", "2020.01")),
    ("2011.06.10 ~ 2011.11.15", ("2011.06", "2011.11")),
    ("2008.03.01 ~ 2008.09.30", ("2008.03", "2008.09")),
)


def _project(
    name="데이터 플랫폼 구축",
    customer="고객A",
    start="2099.01",
    end="2099.06",
    *,
    quote="PL",
):
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        duration_months=6,
        source_refs=[SourceRef(document_id=D1, page_no=1, quote_text=quote)],
    )


def _candidate(*projects):
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스터"),
        projects=list(projects),
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _correct(doc, pages, *, flexible: bool):
    allowed: dict[str, set[int]] = {}
    for doc_id, page_no in pages:
        allowed.setdefault(doc_id, set()).add(page_no)
    return correct_project_dates_from_unique_owned_period(
        doc,
        page_texts=pages,
        allowed_documents=allowed,
        flexible_period_parse=flexible,
    )


def test_v29_isolates_flexible_parse_flag_from_v25_v28() -> None:
    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v25"
    assert current_profile_prompt().prompt_version == "profile-extract-v25"

    baseline = resolve_profile_prompt("profile-extract-v25")
    prior = resolve_profile_prompt("profile-extract-v28")
    experiment = resolve_profile_prompt("profile-extract-v29")

    assert experiment.prompt_version == "profile-extract-v29"
    assert experiment.correct_project_dates_from_unique_owned_period is True
    assert experiment.flexible_project_period_parse is True
    assert v29.FLEXIBLE_PROJECT_PERIOD_PARSE is True
    assert prior.correct_project_dates_from_unique_owned_period is True
    assert prior.flexible_project_period_parse is False
    assert baseline.flexible_project_period_parse is False
    assert not hasattr(v28, "FLEXIBLE_PROJECT_PERIOD_PARSE")

    changed = {
        "prompt_version",
        "projects_system_prompt",
        "build_projects_user_prompt",
        "correct_project_dates_from_unique_owned_period",
        "flexible_project_period_parse",
    }
    for field in fields(ProfilePromptSpec):
        if field.name not in changed:
            assert getattr(experiment, field.name) == getattr(baseline, field.name)


def test_legacy_parsers_miss_or_misread_all_eight_real_period_formats() -> None:
    assert len(V29_REAL_PERIOD_FORMATS) == 8
    full_misses = 0
    for raw, expected in V29_REAL_PERIOD_FORMATS:
        if _PROJECT_FULL_RANGE_RE.search(raw) is None:
            full_misses += 1
        legacy = parse_project_date_range_from_texts([raw])
        assert legacy != expected
    assert full_misses == 3


def test_flexible_parser_normalizes_all_eight_real_period_formats() -> None:
    for raw, expected in V29_REAL_PERIOD_FORMATS:
        assert (
            parse_project_date_range_from_texts(
                [raw], flexible_period_parse=True
            )
            == expected
        )


def test_flexible_parser_fail_closed_on_omitted_year_wrap_and_conflicts() -> None:
    # End month before start month without end year is ambiguous.
    assert (
        parse_project_date_range_from_texts(
            ["2023.10.1~2.28"], flexible_period_parse=True
        )
        is None
    )
    assert (
        parse_project_date_range_from_texts(
            ["2023.4.1~6.30", "2022.1.3~3.31"],
            flexible_period_parse=True,
        )
        is None
    )


def test_owned_period_correction_uses_flexible_day_and_omitted_year_forms() -> None:
    page = (
        "2023.4.1~6.30 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A\n"
        "2019.07.01 ~ 2020.01.31 | SI | 보안 시스템 개선 | PM | 회사 | 고객B"
    )
    wrong_a = _project(start="2019.07", end="2020.01", quote="PL")
    wrong_b = _project(
        name="보안 시스템 개선",
        customer="고객B",
        start="2023.04",
        end="2023.06",
        quote="PM",
    )
    # Without flexible parse, ownership cannot read these day forms.
    untouched = _correct(
        _candidate(wrong_a, wrong_b), {(D1, 1): page}, flexible=False
    ).projects
    assert [(p.start_date, p.end_date) for p in untouched] == [
        ("2019.07", "2020.01"),
        ("2023.04", "2023.06"),
    ]

    fixed = _correct(
        _candidate(wrong_a, wrong_b), {(D1, 1): page}, flexible=True
    ).projects
    assert [(p.start_date, p.end_date, p.duration_months) for p in fixed] == [
        ("2023.04", "2023.06", 2),
        ("2019.07", "2020.01", 6),
    ]


def test_flexible_correction_fail_closed_on_duplicate_identity() -> None:
    page = "2023.4.1~6.30 | SI | 데이터 플랫폼 구축 | PL | 회사 | 고객A"
    a = _project(start="2019.07", end="2020.01", quote="PL")
    dup = _project(start="2019.07", end="2020.01", quote="PL")
    out = _correct(_candidate(a, dup), {(D1, 1): page}, flexible=True)
    assert [(p.start_date, p.end_date) for p in out.projects] == [
        ("2019.07", "2020.01"),
        ("2019.07", "2020.01"),
    ]
