"""v25 regression: source section authority prevents v24 category leakage."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    CertificationCandidate,
    EducationCandidate,
    EmploymentCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)
from app.modules.analysis.candidate_quality_guards import (
    apply_candidate_quality_guards,
)

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TYPES = {_DOC: "DOC-PROFILE"}
_PAGES = {
    (_DOC, 1): (
        "학력사항\n"
        "학교명 전공 학위\n"
        "한빛대학교 컴퓨터공학 학사\n"
        "자격증명 취득일\n"
        "정보처리기사 2019.01.05\n"
        "회사명 기간 직위 담당업무\n"
        "네모소프트(주) 2007.07 ~ 2011.01 사원 BI/DW 개발\n"
        "그린솔루션 2012.01 ~ 2014.12 과장 DB 개발\n"
    ),
    (_DOC, 3): (
        "프로젝트 수행내역\n"
        "사업명 기간 고객사 수행역할\n"
        "회계시스템 고도화 2016.04 ~ 2016.07 에이원공사 설계/개발\n미반영 장부정리 2016.07 ~ 2016.12 금융결제원 개발\n"
        "정보분석 2015.01 ~ 2015.03 에이원공사 SAP BO 3.1 TeraStream\n"
    ),
    (_DOC, 5): "상기 프로필은 소프트웨어 기술자 증명 자료입니다.\n",
}


def _candidate() -> ProfileCandidateDocument:
    return ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="네모소프트(주)",
                start_date="2007.07",
                end_date="2011.01",
            ),
            EmploymentCandidate(
                company_name="그린솔루션",
                start_date=None,
                end_date="2014.12",
            ),
            EmploymentCandidate(
                company_name="금융결제원",
                start_date=None,
                end_date="2016.07",
            ),
        ],
        education=[
            EducationCandidate(school_name="한빛대학교", degree="학사"),
            EducationCandidate(school_name="○○대학교", degree="학사"),
            EducationCandidate(school_name="금융결제원", degree="학사"),
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사", acquired_date="2019.01.05"
            ),
            CertificationCandidate(
                certification_name="SAP BO 3.1", acquired_date="2015.01"
            ),
            CertificationCandidate(
                certification_name="TeraStream", acquired_date="2015.03"
            ),
            CertificationCandidate(certification_name="소프트웨어 기술자"),
        ],
        projects=[
            ProjectCandidate(
                project_name="네모소프트(주)",
                customer_name="네모소프트(주)",
                start_date="2007.07",
                end_date="2011.01",
                responsibilities="BI/DW 개발",
                source_refs=[
                    SourceRef(
                        document_id=_DOC, page_no=1, quote_text="BI/DW 개발"
                    )
                ],
            ),
            ProjectCandidate(
                project_name="회계시스템 고도화",
                customer_name="에이원공사",
                start_date="2016.04",
                end_date="2016.07",
                source_refs=[
                    SourceRef(
                        document_id=_DOC,
                        page_no=3,
                        quote_text="회계시스템 고도화",
                    )
                ],
            ),
        ],
    )


def test_v25_removes_only_confident_cross_section_leaks() -> None:
    candidate = _candidate()
    guarded = apply_candidate_quality_guards(
        candidate,
        page_texts=_PAGES,
        document_type_by_id=_TYPES,
        strict_section_authority=True,
    )
    assert [x.company_name for x in guarded.employment_history] == [
        "네모소프트(주)",
        "그린솔루션",
    ]
    assert [x.school_name for x in guarded.education] == ["한빛대학교"]
    assert [x.certification_name for x in guarded.certifications] == [
        "정보처리기사"
    ]
    assert [x.project_name for x in guarded.projects] == [
        "회계시스템 고도화"
    ]
    # New run guard never changes the original candidate in memory.
    assert len(candidate.employment_history) == 3
    assert len(candidate.projects) == 2


def test_v24_remains_compatible_when_strict_flag_off() -> None:
    candidate = _candidate()
    baseline = apply_candidate_quality_guards(
        candidate, page_texts=_PAGES, document_type_by_id=_TYPES
    )
    # Customer in employment does not match any retained project customer,
    # hence the old guard alone cannot identify the v24 leak.
    assert any(x.company_name == "금융결제원" for x in baseline.employment_history)
    assert any(x.school_name == "○○대학교" for x in baseline.education)
    assert any(
        x.certification_name == "SAP BO 3.1"
        for x in baseline.certifications
    )
    assert any(x.project_name == "네모소프트(주)" for x in baseline.projects)


def test_v25_missing_section_authority_fails_open() -> None:
    candidate = _candidate()
    guarded = apply_candidate_quality_guards(
        candidate,
        page_texts={(_DOC, 1): "비정형 프로젝트 및 경력 텍스트"},
        strict_section_authority=True,
    )
    assert guarded.education == candidate.education
    assert guarded.certifications == candidate.certifications
    assert guarded.projects == candidate.projects


def test_v25_real_certificate_and_project_customer_stay_separate() -> None:
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="에이원공사",
                start_date="2010.01",
                end_date="2012.02",
            )
        ],
        certifications=[
            CertificationCandidate(
                certification_name="SAP BO 3.1",
                acquired_date="2010.11",
            ),
        ],
        projects=[
            ProjectCandidate(
                project_name="SAP BO 3.1 구축",
                customer_name="에이원공사",
                start_date="2015.01",
                end_date="2015.03",
            )
        ],
    )
    pages = {
        (_DOC, 1): (
            "회사명 기간 직위 담당업무\n"
            "에이원공사 2010.01 ~ 2012.02 과장\n"
            "자격증명 취득일\n"
            "SAP BO 3.1 2010.11\n"
        ),
        (_DOC, 3): (
            "프로젝트 수행내역\n"
            "SAP BO 3.1 구축 2015.01 ~ 2015.03 에이원공사\n"
        ),
    }
    guarded = apply_candidate_quality_guards(
        candidate, page_texts=pages,
        document_type_by_id=_TYPES, strict_section_authority=True,
    )
    assert guarded.employment_history == candidate.employment_history
    assert guarded.certifications == candidate.certifications
    assert guarded.projects == candidate.projects
