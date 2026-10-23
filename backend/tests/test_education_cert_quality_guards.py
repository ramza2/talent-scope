"""CORE education / certification quality guards (dedupe, OCR, issuer)."""

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
from app.modules.analysis.compact_v8 import merge_core_chunk_candidates

_DOCX = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_KOSA = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _page_texts_hallym() -> dict[tuple[str, int], str]:
    return {
        (_DOCX, 1): (
            "학력\n"
            "한림대학교 유전공학 1994.03.02 ~ 1999.02.20 학사\n"
            "자격증\n"
            "정보처리기사 2006.12.18\n"
            "전자계산기조직응용기사 2008.06.02\n"
        ),
        (_KOSA, 1): (
            "학력\n"
            "한림대학교 / 유전공학 / 1994.03.02~1999.02.20 / 학사\n"
            "자격증\n"
            "경보처리기사 2006.12.18\n"
            "전자계산기조작용용기사 2008.06.02\n"
            "발급기관 한국산업입력공단\n"
        ),
    }


def test_kosa_duplicate_education_merges_and_fills_major_period() -> None:
    page_texts = _page_texts_hallym()
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(
                school_name="한림대학교",
                degree="학사",
                source_refs=[
                    SourceRef(document_id=_KOSA, page_no=1, quote_text="한림대학교")
                ],
            ),
            EducationCandidate(
                school_name="한림대학교",
                major="유전공학",
                degree="학사",
                start_date="1994.03.02",
                end_date="1999.02.20",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="유전공학")
                ],
            ),
        ]
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    assert len(result.education) == 1
    edu = result.education[0]
    assert edu.school_name == "한림대학교"
    assert edu.major == "유전공학"
    assert edu.degree == "학사"
    assert edu.start_date == "1994.03.02"
    assert edu.end_date == "1999.02.20"
    assert {r.document_id for r in edu.source_refs} == {_DOCX, _KOSA}


def test_same_school_distinct_degree_or_major_preserved() -> None:
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(
                school_name="한림대학교",
                major="유전공학",
                degree="학사",
                start_date="1994.03",
                end_date="1999.02",
            ),
            EducationCandidate(
                school_name="한림대학교",
                major="컴퓨터공학",
                degree="석사",
                start_date="2000.03",
                end_date="2002.02",
            ),
        ]
    )
    result = apply_candidate_quality_guards(candidate, page_texts=_page_texts_hallym())
    assert len(result.education) == 2
    assert {e.degree for e in result.education} == {"학사", "석사"}
    assert {e.major for e in result.education} == {"유전공학", "컴퓨터공학"}


def test_docx_kosa_cert_ocr_variants_merge_with_evidence() -> None:
    page_texts = _page_texts_hallym()
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(
                school_name="한림대학교",
                major="유전공학",
                degree="학사",
                start_date="1994.03.02",
                end_date="1999.02.20",
            )
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
                issuer="한림대학교",
                source_refs=[
                    SourceRef(document_id=_DOCX, page_no=1, quote_text="정보처리기사")
                ],
            ),
            CertificationCandidate(
                certification_name="경보처리기사",
                acquired_date="2006.12.18",
                source_refs=[
                    SourceRef(document_id=_KOSA, page_no=1, quote_text="경보처리기사")
                ],
            ),
            CertificationCandidate(
                certification_name="전자계산기조직응용기사",
                acquired_date="2008.06.02",
            ),
            CertificationCandidate(
                certification_name="전자계산기조작용용기사",
                acquired_date="2008.06.02",
            ),
        ],
    )
    result = apply_candidate_quality_guards(
        candidate,
        page_texts=page_texts,
        document_type_by_id={_DOCX: "DOC-PROFILE", _KOSA: "DOC-KOSA"},
    )
    names = sorted(c.certification_name or "" for c in result.certifications)
    assert names == ["전자계산기조직응용기사", "정보처리기사"]
    by_name = {c.certification_name: c for c in result.certifications}
    assert by_name["정보처리기사"].acquired_date == "2006.12.18"
    assert by_name["전자계산기조직응용기사"].acquired_date == "2008.06.02"
    # School must not stick as issuer; do not invent 한국산업인력공단.
    assert by_name["정보처리기사"].issuer is None
    assert all(c.issuer != "한국산업인력공단" for c in result.certifications)
    assert all(c.issuer != "한국산업입력공단" for c in result.certifications)


def test_same_date_distinct_certifications_preserved() -> None:
    page_texts = {
        (_DOCX, 1): (
            "자격증\n"
            "정보처리기사 2006.12.18\n"
            "전자계산기조직응용기사 2006.12.18\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
            ),
            CertificationCandidate(
                certification_name="전자계산기조직응용기사",
                acquired_date="2006.12.18",
            ),
        ]
    )
    result = apply_candidate_quality_guards(candidate, page_texts=page_texts)
    assert len(result.certifications) == 2


def test_school_name_not_used_as_cert_issuer_without_row_evidence() -> None:
    page_texts = {
        (_DOCX, 1): (
            "학력\n"
            "한림대학교 유전공학 1994.03.02 ~ 1999.02.20 학사\n"
            "자격증\n"
            "정보처리기사 2006.12.18\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(school_name="한림대학교", degree="학사")
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
                issuer="한림대학교",
            )
        ],
    )
    result = apply_candidate_quality_guards(candidate, page_texts=page_texts)
    assert len(result.certifications) == 1
    assert result.certifications[0].issuer is None


def test_no_ungrounded_issuer_or_ocr_merge_without_pages() -> None:
    """Without page evidence, OCR variants must not collapse; no issuer invent."""
    candidate = ProfileCandidateDocument(
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                acquired_date="2006.12.18",
            ),
            CertificationCandidate(
                certification_name="경보처리기사",
                acquired_date="2006.12.18",
            ),
        ]
    )
    result = apply_candidate_quality_guards(candidate)
    assert len(result.certifications) == 2
    assert all(c.issuer is None for c in result.certifications)


def test_merge_core_education_missing_start_still_merges() -> None:
    a = ProfileCandidateDocument(
        education=[
            EducationCandidate(school_name="한림대학교", degree="학사")
        ]
    )
    b = ProfileCandidateDocument(
        education=[
            EducationCandidate(
                school_name="한림대학교",
                major="유전공학",
                degree="학사",
                start_date="1994.03.02",
                end_date="1999.02.20",
            )
        ]
    )
    merged = merge_core_chunk_candidates([a, b])
    assert len(merged.education) == 1
    assert merged.education[0].major == "유전공학"
    assert merged.education[0].start_date == "1994.03.02"


def test_employment_and_project_guards_unchanged_with_edu_cert() -> None:
    page_texts = {
        (_DOCX, 1): (
            "학력\n한림대학교 유전공학 1994.03.02 ~ 1999.02.20 학사\n"
            "근무경력\n피엔드티컨설팅(주) 2010.03 ~ 2014.09 과장\n"
        ),
        (_DOCX, 2): (
            "수행내역\n농지전산 고도화 한국농어촌공사 2020.01 ~ 2022.06\n"
        ),
    }
    candidate = ProfileCandidateDocument(
        profile=ProfileCandidate(name="홍길동"),
        education=[
            EducationCandidate(school_name="한림대학교", degree="학사"),
            EducationCandidate(
                school_name="한림대학교",
                major="유전공학",
                degree="학사",
                start_date="1994.03.02",
                end_date="1999.02.20",
            ),
        ],
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
        document_type_by_id={_DOCX: "DOC-PROFILE"},
    )
    assert len(result.education) == 1
    assert result.education[0].major == "유전공학"
    assert [e.company_name for e in result.employment_history] == [
        "피엔드티컨설팅(주)"
    ]
    assert [p.project_name for p in result.projects] == ["농지전산 고도화"]
