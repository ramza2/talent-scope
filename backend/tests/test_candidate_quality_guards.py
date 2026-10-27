"""Regression for fail-closed candidate field / pseudo-project filtering."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    CertificationCandidate,
    EducationCandidate,
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



def test_short_company_name_does_not_match_other_employer_name() -> None:
    """KT in a customer row must not be validated by an SKT employer row."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명 기간 직위\n"
            "SKT 2010.01 ~ 2012.12 대리\n"
        ),
        (_DOCX, 2): (
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
            "데이터 사업 KT 2020.01 ~ 2021.01\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(company_name="SKT", start_date="2010.01", end_date="2012.12"),
            EmploymentCandidate(company_name="KT", start_date="2020.01", end_date="2021.01"),
        ],
        projects=[
            ProjectCandidate(
                project_name="데이터 사업", customer_name="KT",
                start_date="2020.01", end_date="2021.01",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate, page_texts=page_texts, document_type_by_id={_DOCX: "DOC-RESUME"}
    )
    assert [row.company_name for row in result.employment_history] == ["SKT"]


def test_flattened_mixed_employer_project_line_keeps_candidate() -> None:
    """Flattened OCR with both row types is ambiguous, not project-only."""
    page_texts = {
        (_KOSA, 1): (
            "회사명 기간 직위 피엔드티컨설팅(주) 2012.12 ~ 2014.09 수석 "
            "프로젝트 수행내역 사업명 고객사 피엔드티컨설팅(주) "
            "2016.01 ~ 2016.12\n"
        )
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2012.12", end_date="2014.09",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="시스템 고도화", customer_name="피엔드티컨설팅(주)",
                start_date="2016.01", end_date="2016.12",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate, page_texts=page_texts, document_type_by_id={_KOSA: "DOC-KOSA"}
    )
    assert len(result.employment_history) == 1
    assert result.employment_history[0].end_date == "2014.09"


def test_ambiguous_employer_mention_prevents_project_only_auto_drop() -> None:
    """Do not discard a candidate if independent context is inconclusive."""
    page_texts = {
        (_DOCX, 1): "이력 관련 참고\n에이비씨테크 2010.01 ~ 2012.12\n",
        (_DOCX, 2): (
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
            "고객 분석 에이비씨테크 2020.01 ~ 2021.12\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="에이비씨테크",
                start_date="2010.01", end_date="2012.12",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="고객 분석", customer_name="에이비씨테크",
                start_date="2020.01", end_date="2021.12",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate, page_texts=page_texts, document_type_by_id={_DOCX: "DOC-RESUME"}
    )
    assert len(result.employment_history) == 1
    assert result.employment_history[0].company_name == "에이비씨테크"


def test_docx_later_page_prior_drops_customer_without_section_header() -> None:
    """DOCX p2+ defaults to project; dated customer rows drop without headers."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
        ),
        # No explicit 수행내역 header — page prior must still treat as project.
        (_DOCX, 3): "한국농어촌공사 2020.01 ~ 2022.06 시스템 구축\n",
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="시스템 구축",
                customer_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert [e.company_name for e in result.employment_history] == [
        "피엔드티컨설팅(주)"
    ]


def test_kosa_collapsed_line_splits_employment_from_project_customer() -> None:
    """KOSA one-line mix: keep 근무경력 employer, drop 수행내역 customer."""
    page_texts = {
        (_KOSA, 3): (
            "근무경력 피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장 "
            "수행내역 사업명 고객사 한국농어촌공사 2020.01 ~ 2022.06\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2020.01",
                end_date="2022.06",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="농지전산",
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


def test_project_page_dates_do_not_drive_employment_end_review() -> None:
    """Same company on a project page must not invent an end-date conflict."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
        ),
        (_DOCX, 2): (
            "수행내역\n"
            "별도사업 피엔드티컨설팅(주) 2023.01 ~ 2025.09\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="별도사업",
                customer_name="피엔드티컨설팅(주)",
                start_date="2023.01",
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
    assert emp.end_date == "2014.09"
    assert employment_candidate_dates_need_review(emp) is False


def test_kosa_p2_headerless_project_customers_dropped() -> None:
    """KOSA p2 headerless dated rows are project history, not employers."""
    page_texts = {
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
            "프로젝트 수행내역\n"
            "사업명 고객사 기간\n"
        ),
        (_KOSA, 2): (
            "확인 제주은행 차세대 ISP 2018.08.27 ~ 2018.10.26\n"
            "확인 KT 클라우드 전환 2019.01.01 ~ 2019.06.30\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="제주은행",
                start_date="2018.08.27",
                end_date="2018.10.26",
            ),
            EmploymentCandidate(
                company_name="KT",
                start_date="2019.01.01",
                end_date="2019.06.30",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="차세대 ISP",
                customer_name="제주은행",
                start_date="2018.08.27",
                end_date="2018.10.26",
            ),
            ProjectCandidate(
                project_name="클라우드 전환",
                customer_name="KT",
                start_date="2019.01.01",
                end_date="2019.06.30",
            ),
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


def test_docx_and_kosa_same_project_customer_dropped() -> None:
    """Shared DOCX/KOSA project customer must not become employment."""
    page_texts = {
        (_DOCX, 2): (
            "수행내역\n"
            "차세대 ISP 제주은행 2018.08 ~ 2018.10\n"
        ),
        (_KOSA, 2): "확인 제주은행 차세대 ISP 2018.08.27 ~ 2018.10.26\n",
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="제주은행",
                start_date="2018.08",
                end_date="2018.10",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="차세대 ISP",
                customer_name="제주은행",
                start_date="2018.08",
                end_date="2018.10",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert [e.company_name for e in result.employment_history] == [
        "피엔드티컨설팅(주)"
    ]


def test_kosa_p1_real_employer_preserved_with_later_project_pages() -> None:
    """KOSA p1 근무경력 employer survives headerless project pages."""
    page_texts = {
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 오픈링크 2015.01 ~ 2020.12 책임연구원\n"
            "프로젝트 수행내역\n"
        ),
        (_KOSA, 3): "확인 제주은행 차세대 ISP 2018.08.27 ~ 2018.10.26\n",
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="오픈링크",
                start_date="2015.01",
                end_date="2020.12",
                title="책임연구원",
            ),
            EmploymentCandidate(
                company_name="제주은행",
                start_date="2018.08.27",
                end_date="2018.10.26",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="차세대 ISP",
                customer_name="제주은행",
                start_date="2018.08.27",
                end_date="2018.10.26",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_KOSA: "DOC-KOSA"},
    )
    assert [e.company_name for e in result.employment_history] == ["오픈링크"]
    assert result.employment_history[0].end_date == "2020.12"


def test_employer_also_project_customer_kept() -> None:
    """Company that is both real employer and a project customer is kept."""
    page_texts = {
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
            "프로젝트 수행내역\n"
        ),
        (_KOSA, 2): (
            "확인 피엔드티컨설팅(주) 시스템 고도화 2016.01 ~ 2016.12\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="시스템 고도화",
                customer_name="피엔드티컨설팅(주)",
                start_date="2016.01",
                end_date="2016.12",
            )
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    assert result.employment_history[0].company_name == "피엔드티컨설팅(주)"
    assert result.employment_history[0].end_date == "2014.09"


def test_company_match_key_strips_only_edge_corp_forms() -> None:
    from app.modules.analysis.candidate_quality_guards import _company_match_key

    assert _company_match_key("비스프로컨설팅(주)") == "비스프로컨설팅"
    assert _company_match_key("비스프로컨설팅 주식회사") == "비스프로컨설팅"
    assert _company_match_key("주식회사 비스프로컨설팅") == "비스프로컨설팅"
    assert _company_match_key("Prince") == "prince"
    assert _company_match_key("Pre") == "pre"
    assert _company_match_key("Prince") != _company_match_key("Pre")
    assert _company_match_key("Prince Inc") == "prince"
    assert _company_match_key("Inc Prince") == "incprince"  # Inc not a suffix here
    # Internal Latin fragments must not collapse Zinc→Z or similar.
    assert _company_match_key("Zinc") == "zinc"
    assert _company_match_key("Z") == "z"
    assert _company_match_key("Zinc") != _company_match_key("Z")
    assert _company_match_key("Zinc Inc") == "zinc"
    assert _company_match_key("Zinc Inc") != _company_match_key("Z")
    assert _company_match_key("Acme Corp") == "acme"
    assert _company_match_key("Acme LLC") == "acme"
    assert _company_match_key("Acme Co., Ltd") == "acme"
    assert _company_match_key("CorpAcme") == "corpacme"  # Corp not independent
    assert _company_match_key("(주)") is None
    assert _company_match_key("주식회사") is None


def _kwon_seven_employment_page_texts() -> dict[tuple[str, int], str]:
    """Real DOCX p1 company→period cells + KOSA one-line employer row."""
    return {
        (_DOCX, 1): (
            "근무경력\n"
            "회사명\n"
            "기간\n"
            "직위\n"
            "담당업무\n"
            "피엔드티컨설팅(주)\n"
            "2012.12 ~ 2014.09\n"
            "과장\n"
            "시스템 운영\n"
            "비스프로컨설팅(주)\n"
            "2011.02 ~ 2012.11\n"
            "과장\n"
            "SI 업무\n"
            "네모소프트(주)\n"
            "2007.07 ~ 2011.01\n"
            "사원\n"
            "개발\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 비스프로컨설팅(주) 2011.02.01 ~ 2012.11.30 과장\n"
        ),
    }


def test_kwon_seven_employment_dedupe_bispro_only() -> None:
    """권민규 7건: DOCX 셀 페어링 + KOSA 한 줄, 오연결 refs여도 비스프로만 병합."""
    page_texts = _kwon_seven_employment_page_texts()
    # Both rows incorrectly anchored to DOCX p1 (ops mislink of the KOSA row).
    mislinked_docx = [
        SourceRef(
            document_id=_DOCX,
            page_no=1,
            quote_text="비스프로컨설팅(주)",
        )
    ]
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(school_name="한림대학교", degree="학사"),
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
            ),
            CertificationCandidate(
                certification_name="전자계산기조직응용기사",
                acquired_date="2008.06.02",
            ),
        ],
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
                source_refs=list(mislinked_docx),
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02.01",
                end_date="2012.11.30",
                source_refs=list(mislinked_docx),
            ),
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2012.12",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="피앤드티컨설팅",
                start_date="2008.01",
                end_date="2009.12",
            ),
            EmploymentCandidate(
                company_name="네모소프트(주)",
                start_date="2007.07",
                end_date="2011.01",
            ),
            EmploymentCandidate(
                company_name="모스트",
                start_date="2016.03",
                end_date="2017.12",
            ),
            EmploymentCandidate(
                company_name="오픈링크",
                start_date="2015.01",
                end_date="2020.12",
            ),
        ],
        projects=[
            ProjectCandidate(project_name=f"사업{i}", start_date="2020.01")
            for i in range(70)
        ],
    )
    assert len(candidate.employment_history) == 7

    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.education) == 1
    assert len(result.certifications) == 2
    assert len(result.projects) == 70
    assert len(result.employment_history) == 6

    companies = [e.company_name or "" for e in result.employment_history]
    assert any(c.startswith("피엔드티컨설팅") for c in companies)
    assert "피앤드티컨설팅" in companies
    assert any(c.startswith("네모소프트") for c in companies)
    assert "모스트" in companies
    bispro = [
        e
        for e in result.employment_history
        if (e.company_name or "").startswith("비스프로컨설팅")
    ]
    assert len(bispro) == 1
    # Month vs day precision is compatible — not a REVIEW conflict.
    assert employment_candidate_dates_need_review(bispro[0]) is False
    assert bispro[0].end_date in {"2012.11", "2012.11.30"}
    assert {r.document_id for r in bispro[0].source_refs} == {_DOCX, _KOSA}
    docx_refs = [r for r in bispro[0].source_refs if r.document_id == _DOCX]
    assert any(
        "비스프로컨설팅(주)" in (r.quote_text or "")
        and "2011.02 ~ 2012.11" in (r.quote_text or "")
        for r in docx_refs
    )
    kosa_refs = [r for r in bispro[0].source_refs if r.document_id == _KOSA]
    assert len(kosa_refs) == 1
    assert kosa_refs[0].page_no == 1
    assert "비스프로컨설팅" in (kosa_refs[0].quote_text or "")
    assert "2011.02.01" in (kosa_refs[0].quote_text or "")
    assert "2012.11.30" in (kosa_refs[0].quote_text or "")

    again = apply_candidate_quality_guards(
        result,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert again == result


def test_employment_docx_cell_pairing_adjacent_company_period_succeeds() -> None:
    """Immediate company→period cells on the same employment page pair."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "비스프로컨설팅(주)\n"
            "2011.02 ~ 2012.11\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 비스프로컨설팅(주) 2011.02.01 ~ 2012.11.30 과장\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="x")
                ],
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02.01",
                end_date="2012.11.30",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="x")
                ],
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    emp = result.employment_history[0]
    assert {r.document_id for r in emp.source_refs} == {_DOCX, _KOSA}
    assert any(
        "비스프로컨설팅(주)" in (r.quote_text or "")
        and "2011.02 ~ 2012.11" in (r.quote_text or "")
        for r in emp.source_refs
        if r.document_id == _DOCX
    )


def test_employment_docx_cell_pairing_does_not_borrow_other_company_dates() -> None:
    """Company cell may only bind the immediate next date-range cell."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "피엔드티컨설팅(주)\n"
            "2012.12 ~ 2014.09\n"
            "비스프로컨설팅(주)\n"
            "2011.02 ~ 2012.11\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                # Wrong period belonging to the prior company row.
                start_date="2012.12",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅 주식회사",
                start_date="2012.12",
                end_date="2014.09",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    # No clear period match for 비스프로 at 2012.12~2014.09 → no merge.
    assert len(result.employment_history) == 2


def test_employment_docx_pairing_rejects_intervening_infohwa_cell() -> None:
    """Company → 정보화 → period must not pair across the intervening cell."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "비스프로컨설팅(주)\n"
            "정보화\n"
            "2011.02 ~ 2012.11\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 비스프로컨설팅(주) 2011.02.01 ~ 2012.11.30 과장\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅 주식회사",
                start_date="2011.02.01",
                end_date="2012.11.30",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    # DOCX pairing blocked; only KOSA one-line evidence → not independent dual.
    assert len(result.employment_history) == 2


def test_employment_docx_pairing_rejects_intervening_project_cell() -> None:
    """Company → project cell → period must not pair across the project span."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "비스프로컨설팅(주)\n"
            "수행내역\n"
            "2011.02 ~ 2012.11\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 비스프로컨설팅(주) 2011.02.01 ~ 2012.11.30 과장\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅 주식회사",
                start_date="2011.02.01",
                end_date="2012.11.30",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 2


def test_employment_dedupe_rejects_fuzzy_and_distinct_periods() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "한국농어촌공사 2010.01 ~ 2012.12 사원\n"
            "한국농어촌 2010.01 ~ 2012.12 사원\n"
            "에이비씨테크 2010.01 ~ 2012.12 사원\n"
            "에이비씨테크 2015.01 ~ 2018.06 대리\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="한국농어촌공사",
                start_date="2010.01",
                end_date="2012.12",
            ),
            EmploymentCandidate(
                company_name="한국농어촌",  # stem-similar, must NOT merge
                start_date="2010.01",
                end_date="2012.12",
            ),
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
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 4


def test_employment_dedupe_without_page_evidence_keeps_both() -> None:
    """Corporate-form variants without employment-section evidence stay separate."""
    page_texts = {
        (_DOCX, 2): (
            "수행내역\n"
            "사업X 고객Y 2020.01 ~ 2021.01\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="피엔드티컨설팅(주)",
                start_date="2010.03",
                end_date="2014.09",
            ),
            EmploymentCandidate(
                company_name="피엔드티컨설팅 주식회사",
                start_date="2010.03",
                end_date="2014.09",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2


def test_employment_dedupe_empty_company_key_not_merged() -> None:
    page_texts = {
        (_DOCX, 1): "근무경력\n(주) 2010.01 ~ 2011.01 사원\n주식회사 2010.01 ~ 2011.01 사원\n",
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="(주)",
                start_date="2010.01",
                end_date="2011.01",
            ),
            EmploymentCandidate(
                company_name="주식회사",
                start_date="2010.01",
                end_date="2011.01",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2


def test_prince_pre_not_merged_via_internal_inc() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "Prince 2010.01 ~ 2012.12 사원\n"
            "Pre 2010.01 ~ 2012.12 사원\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="Prince",
                start_date="2010.01",
                end_date="2012.12",
            ),
            EmploymentCandidate(
                company_name="Pre",
                start_date="2010.01",
                end_date="2012.12",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2
    assert {e.company_name for e in result.employment_history} == {"Prince", "Pre"}


def test_zinc_z_not_merged_via_internal_inc() -> None:
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "Zinc Inc 2010.01 ~ 2012.12 사원\n"
            "Z 2010.01 ~ 2012.12 사원\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="Zinc Inc",
                start_date="2010.01",
                end_date="2012.12",
            ),
            EmploymentCandidate(
                company_name="Z",
                start_date="2010.01",
                end_date="2012.12",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2
    assert {e.company_name for e in result.employment_history} == {"Zinc Inc", "Z"}


def test_employment_dedupe_shared_source_line_not_independent_evidence() -> None:
    """One original employer row must not dual-evidence corp-form variants."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "비스프로컨설팅(주)\n"
            "2011.02 ~ 2012.11\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2011.02",
                end_date="2012.11",
                source_refs=[
                    SourceRef(
                        document_id=_DOCX,
                        page_no=1,
                        quote_text="비스프로컨설팅(주)",
                    )
                ],
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅 주식회사",
                start_date="2011.02",
                end_date="2012.11",
                source_refs=[
                    SourceRef(
                        document_id=_DOCX,
                        page_no=1,
                        quote_text="비스프로컨설팅(주)",
                    )
                ],
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.employment_history) == 2


def test_employment_dedupe_multiple_periods_refuse_merge() -> None:
    """Year-precision candidate matching multiple incompatible rows → no merge."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "에이비씨테크 2010.01 ~ 2012.06 사원\n"
            "에이비씨테크 2010.03 ~ 2012.11 대리\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 에이비씨테크 2010.01 ~ 2012.06 사원\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="에이비씨테크",
                start_date="2010",
                end_date="2012",
            ),
            EmploymentCandidate(
                company_name="에이비씨테크(주)",
                start_date="2010",
                end_date="2012",
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 2


def test_employment_dedupe_end_conflict_keeps_review() -> None:
    """Real end-date clash across DOCX/KOSA stays REVIEW after merge."""
    page_texts = {
        (_DOCX, 1): (
            "근무경력\n"
            "비스프로컨설팅(주) 2012.01 ~ 2015.06 과장\n"
        ),
        (_KOSA, 1): (
            "근무경력\n"
            "근무처 비스프로컨설팅 주식회사 2012.01 ~ 2016.12 과장\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="비스프로컨설팅(주)",
                start_date="2012.01",
                end_date="2015.06",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="x")
                ],
            ),
            EmploymentCandidate(
                company_name="비스프로컨설팅 주식회사",
                start_date="2012.01",
                end_date="2016.12",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="x")
                ],
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    emp = result.employment_history[0]
    assert "2015.06" in (emp.end_date or "")
    assert "2016.12" in (emp.end_date or "")
    assert employment_candidate_dates_need_review(emp) is True
    assert {r.document_id for r in emp.source_refs} == {_DOCX, _KOSA}
