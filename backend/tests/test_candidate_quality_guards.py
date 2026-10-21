"""Regression for fail-closed candidate field / pseudo-project filtering."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
)
from app.modules.analysis.candidate_quality_guards import (
    apply_candidate_quality_guards,
)


def test_invalid_email_address_dropped_without_touching_real_email() -> None:
    bad = ProfileCandidateDocument(
        profile=ProfileCandidate(
            name="홍길동",
            email="광주광역시 북구 상세주소 123",
            source_refs={"email": []},
        )
    )
    result = apply_candidate_quality_guards(bad)
    assert result.profile.email is None
    assert "email" not in result.profile.source_refs
    assert bad.profile.email == "광주광역시 북구 상세주소 123"

    valid = ProfileCandidateDocument(
        profile=ProfileCandidate(email="dev+profile@example.co.kr")
    )
    assert apply_candidate_quality_guards(valid).profile.email == "dev+profile@example.co.kr"


def test_total_career_duration_not_employment_type() -> None:
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(
            employment_type="18년 7개월",
            career_document_value="18년 7개월",
        )
    )
    result = apply_candidate_quality_guards(candidate)
    assert result.profile.employment_type is None
    assert result.profile.career_document_value == "18년 7개월"
    assert candidate.profile.employment_type == "18년 7개월"

    legitimate = ProfileCandidateDocument(
        profile=ProfileCandidate(employment_type="정규직")
    )
    assert apply_candidate_quality_guards(legitimate).profile.employment_type == "정규직"


def test_person_header_is_not_project_but_real_project_preserved() -> None:
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길동"),
        projects=[
            ProjectCandidate(project_name="홍 길 동"),
            ProjectCandidate(
                project_name="장성군 농지전산 시스템 운영 및 유지보수",
                customer_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
        ],
    )
    result = apply_candidate_quality_guards(candidate)
    assert [p.project_name for p in result.projects] == [
        "장성군 농지전산 시스템 운영 및 유지보수"
    ]
    assert len(candidate.projects) == 2


def test_customer_only_duplicate_is_not_project_and_dated_row_survives() -> None:
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길동"),
        projects=[
            ProjectCandidate(project_name="한국농어촌공사"),
            ProjectCandidate(
                project_name="농지전산 시스템 운영",
                customer_name="한국농어촌공사",
                start_date="2020.01",
            ),
            ProjectCandidate(
                project_name="한국농어촌공사",
                customer_name="한국농어촌공사",
                start_date="2018.01",
                responsibilities="시스템 고도화",
            ),
        ],
    )
    result = apply_candidate_quality_guards(candidate)
    assert [p.project_name for p in result.projects] == [
        "농지전산 시스템 운영",
        "한국농어촌공사",
    ]


def test_confirmed_person_name_filters_project_when_core_name_missing() -> None:
    candidate = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(project_name="홍 길 동"),
            ProjectCandidate(
                project_name="정보시스템 운영 및 유지보수",
                start_date="2020.01",
            ),
        ],
    )
    result = apply_candidate_quality_guards(
        candidate, confirmed_person_name="홍길동"
    )
    assert [project.project_name for project in result.projects] == [
        "정보시스템 운영 및 유지보수"
    ]


def test_no_known_person_or_customer_identity_does_not_drop_projects() -> None:
    candidate = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(project_name="홍길동"),
            ProjectCandidate(project_name="한국농어촌공사"),
        ],
    )
    assert apply_candidate_quality_guards(candidate).projects == candidate.projects
