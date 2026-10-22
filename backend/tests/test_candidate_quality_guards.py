"""Regression for fail-closed candidate field / pseudo-project filtering."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    EmploymentCandidate,
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)
from app.modules.analysis.candidate_quality_guards import (
    apply_candidate_quality_guards,
)
from app.modules.analysis.diff_engine import (
    build_diffs,
    employment_candidate_dates_need_review,
)

_DOCX = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_KOSA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


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


def test_real_employer_kept_customer_only_employment_dropped() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명 기간 직위 담당업무\n"
            "피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장 시스템 운영\n"
        ),
        (_DOCX, 2): (
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
            "농지전산 고도화 한국농어촌공사 2020.01 ~ 2022.06\n"
            "정보화사업 서울특별시 2018.03 ~ 2019.12\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길동"),
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                title="과장",
                start_date="2010.03",
                end_date="2014.09",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="피엔드티컨설팅(주)")
                ],
            ),
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
            EmploymentCandidate(
                company_name="서울특별시",
                start_date="2018.03",
                end_date="2019.12",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="농지전산 고도화",
                customer_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
            ProjectCandidate(
                project_name="정보화사업",
                customer_name="서울특별시",
                start_date="2018.03",
                end_date="2019.12",
            ),
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    companies = [row.company_name for row in result.employment_history]
    assert companies == ["피엔드티컨설팅(주)"]
    assert [p.project_name for p in result.projects] == [
        "농지전산 고도화",
        "정보화사업",
    ]
    # Input candidate must remain untouched (no REVIEWING mutation).
    assert len(candidate.employment_history) == 3


def test_mismatched_employment_end_date_requires_review_not_auto_adopt() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명 기간 직위 담당업무\n"
            "피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장 시스템 운영\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2025.09",  # AI mixed project end
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="별도사업",
                customer_name="외부고객",
                start_date="2023.02",
                end_date="2025.09",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 1
    emp = result.employment_history[0]
    assert emp.company_name == "피엔드티컨설팅(주)"
    assert emp.end_date is not None
    assert "2014.09" in emp.end_date
    assert "2025.09" in emp.end_date
    assert employment_candidate_dates_need_review(emp) is True
    specs = build_diffs(result, {"employment_history": []})
    emp_specs = [s for s in specs if s.entity_type == "EMPLOYMENT"]
    assert emp_specs and all(s.change_type == "REVIEW" for s in emp_specs)
    # Must not silently rewrite to source-only end.
    assert emp.end_date != "2014.09"
    assert candidate.employment_history[0].end_date == "2025.09"


def test_kosa_real_employment_not_dropped_when_also_project_customer() -> None:
    page_texts = {
        (_DOCX, 2): (
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
            "컨설팅지원 오픈링크 2021.01 ~ 2021.12\n"
        ),
        (_KOSA, 1): (
            "경력증명서\n"
            "근무처 오픈링크 2015.01 ~ 2020.12 책임연구원\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="오픈링크",
                start_date="2015.01",
                end_date="2020.12",
                title="책임연구원",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="컨설팅지원",
                customer_name="오픈링크",
                start_date="2021.01",
                end_date="2021.12",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    assert result.employment_history[0].company_name == "오픈링크"
    assert result.employment_history[0].end_date == "2020.12"


def test_kosa_docx_conflicting_employment_periods_kept_for_review() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명 기간 직위\n"
            "에이비씨테크 2018.01 ~ 2020.06 대리\n"
        ),
        (_KOSA, 1): (
            "경력사항\n"
            "에이비씨테크 2018.01 ~ 2021.12 대리\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="에이비씨테크",
                start_date="2018.01",
                end_date="2020.06",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-CAREER", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    emp = result.employment_history[0]
    assert emp.company_name == "에이비씨테크"
    # Conflicting DOCX/KOSA ends → REVIEW token, row preserved (not deleted).
    assert emp.end_date is not None
    assert "2020.06" in emp.end_date
    assert "2021.12" in emp.end_date
    assert employment_candidate_dates_need_review(emp) is True


def test_kosa_project_customer_only_dropped_real_employer_kept() -> None:
    """KOSA doc type alone is not employment evidence; project customers drop."""
    page_texts = {
        (_KOSA, 1): (
            "경력증명서\n"
            "근무처 피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
            "\n"
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
            "농지전산 고도화 한국농어촌공사 2020.01 ~ 2022.06\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
                title="과장",
            ),
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="농지전산 고도화",
                customer_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_KOSA: "DOC-KOSA"},
    )
    assert [e.company_name for e in result.employment_history] == [
        "피엔드티컨설팅(주)"
    ]
    assert result.employment_history[0].end_date == "2014.09"


def test_same_company_distinct_employment_periods_not_false_conflict() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명 기간 직위\n"
            "에이비씨테크 2010.01 ~ 2012.12 사원\n"
            "에이비씨테크 2015.01 ~ 2018.06 대리\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="에이비씨테크",
                start_date="2010.01",
                end_date="2012.12",
            ),
            EmploymentCandidate(
                company_name="에이비씨테크",
                start_date="2015.01",
                end_date="2018.06",
            ),
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2
    by_start = {e.start_date: e.end_date for e in result.employment_history}
    assert by_start == {"2010.01": "2012.12", "2015.01": "2018.06"}


def test_customer_name_without_project_row_evidence_is_kept() -> None:
    """Insufficient structure must not false-delete a possible real employer."""
    page_texts = {
        (_DOCX, 1): "기타 메모\n한국농어촌공사\n",
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="농지전산",
                customer_name="한국농어촌공사",
                start_date="2020.01",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert [e.company_name for e in result.employment_history] == [
        "한국농어촌공사"
    ]


def test_employment_guard_does_not_alter_unrelated_core_or_projects() -> None:
    page_texts = {
        (_DOCX, 1): "근무경력\n정상회사 2010.01 ~ 2012.12 사원\n",
        (_DOCX, 2): "수행내역\n사업X 고객사Y 2020.01 ~ 2021.01\n",
    }
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(
            name="홍길동",
            technical_grade="EXPERT",
            affiliation_company="정상회사",
        ),
        jobs=[],
        skills=[],
        expertise=[],
        employment_history=[
            EmploymentCandidate(
                company_name="정상회사",
                start_date="2010.01",
                end_date="2012.12",
            ),
            EmploymentCandidate(
                company_name="고객사Y",
                start_date="2020.01",
                end_date="2021.01",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="사업X",
                customer_name="고객사Y",
                start_date="2020.01",
                end_date="2021.01",
                responsibilities="운영",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert result.profile.name == "홍길동"
    assert result.profile.technical_grade == "EXPERT"
    assert result.profile.affiliation_company == "정상회사"
    assert [p.project_name for p in result.projects] == ["사업X"]
    assert [e.company_name for e in result.employment_history] == ["정상회사"]
