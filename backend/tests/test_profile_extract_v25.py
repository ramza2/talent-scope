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


def test_v25_page_recall_is_opt_in_only() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    assert resolve_profile_prompt(
        "profile-extract-v25"
    ).project_page_recall_split is True
    assert resolve_profile_prompt(
        "profile-extract-v24"
    ).project_page_recall_split is False
    assert resolve_profile_prompt(
        "profile-extract-v23"
    ).project_page_recall_split is False


def test_page_recall_keeps_independent_rows_and_document_aliases() -> None:
    from app.modules.analysis.token_budget import (
        plan_project_recall_page_chunks,
    )

    source = (
        "[DOCUMENT D1]\\ndocument_id: D1\\ncontent:\\n"
        "[PAGE 3]\\nBW / EIS-Mobile 프로젝트 2013.08~2014.01 한화\\n"
        "[PAGE 4]\\nBW / OLAP 구축 프로젝트 2013.05~2013.07 S-OIL\\n"
        "[PAGE 5]\\nEDW 프로젝트 2006.01~2006.12 대우인터네셔널\\n"
        "[DOCUMENT D2]\\ndocument_id: D2\\ncontent:\\n"
        "[PAGE 2]\\nEDW 프로젝트 2011.10~2011.12 한국전기안전공사\\n"
    ).replace("\\n", "\n")

    chunks = plan_project_recall_page_chunks(source, max_chunks=8)
    assert chunks is not None
    assert len(chunks) == 4
    assert [c.count("[PAGE ") for c in chunks] == [1, 1, 1, 1]
    assert sum("BW / EIS-Mobile" in c for c in chunks) == 1
    assert sum("BW / OLAP" in c for c in chunks) == 1
    assert sum("EDW 프로젝트" in c for c in chunks) == 2
    assert chunks[0].startswith("[DOCUMENT D1]")
    assert chunks[3].startswith("[DOCUMENT D2]")


def test_page_recall_falls_back_when_over_cap_or_without_page_markers() -> None:
    from app.modules.analysis.token_budget import (
        plan_project_recall_page_chunks,
    )

    source = (
        "[DOCUMENT D1]\\ncontent:\\n"
        "[PAGE 1]\\nA 2006.01~2006.02\\n"
        "[PAGE 2]\\nB 2007.01~2007.02\\n"
        "[PAGE 3]\\nC 2008.01~2008.02\\n"
    ).replace("\\n", "\n")

    assert plan_project_recall_page_chunks(source, max_chunks=2) is None
    assert plan_project_recall_page_chunks(
        source, max_chunks=8, max_pages=2
    ) is None
    assert plan_project_recall_page_chunks(
        "[DOCUMENT D1]\\ncontent:\\nno page labels", max_chunks=8
    ) is None


def test_v25_dense_project_page_has_bounded_line_overlap() -> None:
    from app.modules.analysis.token_budget import (
        plan_project_recall_page_chunks,
    )

    # DOCX cell-per-line page: adjacent project labels, periods and customers
    # must remain together in at least one window across the midpoint.
    lines = [
        (
            f"업무 {i:03d} 2013.05~2013.07\n"
            if i % 5 == 0 else f"업무 {i:03d}\n"
        )
        for i in range(120)
    ]
    lines[57:60] = [
        "BW / OLAP 구축 프로젝트\n",
        "기간 2013.05~2013.07\n",
        "고객 S-OIL\n",
    ]
    lines[62:65] = [
        "BW / EIS-Mobile 프로젝트\n",
        "기간 2013.08~2014.01\n",
        "고객 한화\n",
    ]
    body = "".join(lines)
    source = (
        "[DOCUMENT D1]\ndocument_id: D1\ncontent:\n"
        "[PAGE 1]\n고용 정보\n"
        "[PAGE 3]\n" + body + "[PAGE 4]\n자격증 정보\n"
    )

    chunks = plan_project_recall_page_chunks(source, max_chunks=5)
    assert chunks is not None
    assert len(chunks) == 4
    dense_windows = chunks[1:3]
    assert all(
        chunk.startswith(
            "[DOCUMENT D1]\ndocument_id: D1\ncontent:\n[PAGE 3]\n"
        )
        for chunk in dense_windows
    )
    assert all(chunk.count("[PAGE ") == 1 for chunk in chunks)
    for line in lines:
        assert any(line in chunk for chunk in dense_windows)
    for project_lines in (lines[57:60], lines[62:65]):
        assert any(
            all(line in chunk for line in project_lines)
            for chunk in dense_windows
        )
    assert chunks[0].endswith("[PAGE 1]\n고용 정보\n")
    assert chunks[3].endswith("[PAGE 4]\n자격증 정보\n")

    # No spare calls: retain the full original page, never omit or truncate.
    limited = plan_project_recall_page_chunks(source, max_chunks=3)
    assert limited is not None
    assert len(limited) == 3
    assert limited[1].endswith("[PAGE 3]\n" + body)


def test_v25_dense_project_page_skips_nonstructural_source() -> None:
    from app.modules.analysis.token_budget import (
        plan_project_recall_page_chunks,
    )

    # KOSA collapsed rows are unsuitable for line-boundary subdivision.
    dates = " ".join("2013.05~2013.07" for _ in range(40))
    source = (
        "[DOCUMENT D1]\ncontent:\n"
        f"[PAGE 1]\n{dates}\n"
        "[PAGE 2]\n한 줄 문서\n"
        "[PAGE 3]\n한 줄 문서\n"
    )
    chunks = plan_project_recall_page_chunks(source, max_chunks=8)
    assert chunks is not None
    assert len(chunks) == 3
    assert dates in chunks[0]



def test_v25_kosa_structured_seven_column_rows_split_without_loss() -> None:
    from app.modules.analysis.token_budget import plan_project_recall_page_chunks

    table_rows = [
        (
            f"확인 | KOSA 프로젝트 {idx:02d} | "
            "2011.01.01 ~ 2011.02.28 | "
            f"고객사 {idx:02d} | 수행사 | 개발자 | SW개발\n"
        )
        for idx in range(1, 18)
    ]
    kosa_header = (
        "[DOCUMENT D2]\ndocument_id: D2\nfilename: KOSA.pdf\n"
        "document_type: DOC-KOSA\ncontent:\n[PAGE 3]\n"
    )
    resume = (
        "[DOCUMENT D1]\ndocument_id: D1\n"
        "document_type: DOC-RESUME\ncontent:\n"
        "[PAGE 1]\n프로필\n[PAGE 2]\n기술\n"
    )
    source = resume + kosa_header + "".join(table_rows)

    chunks = plan_project_recall_page_chunks(source, max_chunks=8)
    assert chunks is not None
    assert len(chunks) == 6  # 2 résumé pages + 4 KOSA row windows
    kosa_windows = chunks[2:]
    assert all(chunk.startswith(kosa_header) for chunk in kosa_windows)
    assert all(chunk.count("[PAGE ") == 1 for chunk in chunks)
    assert [
        len(chunk.splitlines()) - len(kosa_header.splitlines())
        for chunk in kosa_windows
    ] == [5, 5, 5, 2]
    assert "".join(chunk[len(kosa_header):] for chunk in kosa_windows) == "".join(
        table_rows
    )
    for row in table_rows:
        assert sum(row in chunk for chunk in kosa_windows) == 1

    # One extra call available: no row or source-page loss.
    limited = plan_project_recall_page_chunks(source, max_chunks=4)
    assert limited is not None
    assert len(limited) == 4
    assert "".join(chunk[len(kosa_header):] for chunk in limited[2:]) == "".join(
        table_rows
    )


def test_v25_kosa_row_split_requires_valid_structure_and_doc_type() -> None:
    from app.modules.analysis.token_budget import plan_project_recall_page_chunks

    rows = "".join(
        f"확인 | 프로젝트 {idx} | 2011.01 ~ 2011.02 | 고객 | 수행사 | 개발 | SW개발\n"
        for idx in range(17)
    )
    base = (
        "[DOCUMENT D1]\ndocument_type: DOC-KOSA\ncontent:\n"
        "[PAGE 1]\n" + rows
        + "[PAGE 2]\n기타\n"
        + "[PAGE 3]\n기타\n"
    )

    malformed = base.replace("2011.01 ~ 2011.02", "날짜불명", 1)
    chunks = plan_project_recall_page_chunks(malformed, max_chunks=12)
    assert chunks is not None and len(chunks) == 3
    assert "날짜불명" in chunks[0]

    wrong_type = base.replace("DOC-KOSA", "DOC-RESUME")
    chunks = plan_project_recall_page_chunks(wrong_type, max_chunks=12)
    assert chunks is not None and len(chunks) == 3
    assert rows in chunks[0]



def test_v25_kosa_rows_take_split_budget_before_dense_profile_page() -> None:
    from app.modules.analysis.token_budget import plan_project_recall_page_chunks

    dense = "".join(
        f"이력 {idx:03d} 2010.01~2010.02\n" for idx in range(120)
    )
    kosa_rows = "".join(
        f"확인 | 프로젝트 {idx} | 2011.01 ~ 2011.02 | 고객 | 수행사 | 개발 | SW개발\n"
        for idx in range(17)
    )
    source = (
        "[DOCUMENT D1]\ndocument_type: DOC-RESUME\ncontent:\n"
        "[PAGE 1]\n" + dense
        + "[PAGE 2]\n기술\n"
        + "[DOCUMENT D2]\ndocument_type: DOC-KOSA\ncontent:\n"
        + "[PAGE 3]\n" + kosa_rows
    )
    chunks = plan_project_recall_page_chunks(source, max_chunks=6)
    assert chunks is not None and len(chunks) == 6
    assert dense in chunks[0]
    assert sum("document_type: DOC-KOSA" in c for c in chunks) == 4
