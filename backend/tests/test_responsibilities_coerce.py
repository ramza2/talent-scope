"""Coerce LLM list/string responsibilities into canonical str | None."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    EmploymentCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    coerce_optional_text,
)


def test_coerce_optional_text_helpers() -> None:
    assert coerce_optional_text(None) is None
    assert coerce_optional_text("") is None
    assert coerce_optional_text("  ") is None
    assert coerce_optional_text("  PL / Backend  ") == "PL / Backend"
    assert coerce_optional_text([]) is None
    assert coerce_optional_text(["", "  "]) is None
    assert coerce_optional_text(["설계", "개발"]) == "설계\n개발"
    assert coerce_optional_text([" 설계 ", "", "개발"]) == "설계\n개발"
    assert (
        coerce_optional_text(["설계", {"x": 1}, 123, None, "개발"]) == "설계\n개발"
    )
    assert coerce_optional_text({"x": 1}) is None
    assert coerce_optional_text(123) is None
    assert coerce_optional_text(12.5) is None
    assert coerce_optional_text(True) is None


def test_employment_and_project_responsibilities_list_coerce() -> None:
    # Operational payload shape observed with profile-extract-v5.
    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [
            {
                "company_name": "ABC테크",
                "title": "책임",
                "start_date": "2018-01",
                "end_date": "2020-12",
                "responsibilities": ["AI 서비스 설계", "백엔드 개발"],
            },
            {
                "company_name": "XYZ",
                "title": "선임",
                "responsibilities": ["  PL  ", "", "코드리뷰"],
            },
        ],
        "education": [],
        "certifications": [],
        "projects": [
            {
                "project_name": "공공 RAG",
                "responsibilities": ["요구사항 분석", "검색 파이프라인"],
                "project_summary": ["FTS", "Vector 검색"],
            }
        ],
        "summary": {},
        "analysis": {},
    }
    doc = ProfileCandidateDocument.model_validate(raw)
    assert doc.employment_history[0].responsibilities == "AI 서비스 설계\n백엔드 개발"
    assert doc.employment_history[1].responsibilities == "PL\n코드리뷰"
    assert doc.projects[0].responsibilities == "요구사항 분석\n검색 파이프라인"
    assert doc.projects[0].project_summary == "FTS\nVector 검색"
    assert isinstance(doc.employment_history[0].responsibilities, str)
    assert isinstance(doc.projects[0].responsibilities, str)


def test_responsibilities_string_and_empty_list_regression() -> None:
    emp_str = EmploymentCandidate.model_validate(
        {"company_name": "A", "responsibilities": "  단일 문자열  "}
    )
    assert emp_str.responsibilities == "단일 문자열"

    emp_empty = EmploymentCandidate.model_validate(
        {"company_name": "A", "responsibilities": []}
    )
    assert emp_empty.responsibilities is None

    proj_str = ProjectCandidate.model_validate(
        {
            "project_name": "P",
            "responsibilities": "개발",
            "project_summary": "요약",
        }
    )
    assert proj_str.responsibilities == "개발"
    assert proj_str.project_summary == "요약"

    proj_empty = ProjectCandidate.model_validate(
        {
            "project_name": "P",
            "responsibilities": [],
            "project_summary": [],
        }
    )
    assert proj_empty.responsibilities is None
    assert proj_empty.project_summary is None


def test_v5_guide_marks_text_fields_as_string() -> None:
    from app.ai.prompts.profile_extract_v5 import CANDIDATE_SCHEMA_GUIDE

    assert "responsibilities:string" in CANDIDATE_SCHEMA_GUIDE
    assert "project_summary:string" in CANDIDATE_SCHEMA_GUIDE
    assert "strings, not arrays" in CANDIDATE_SCHEMA_GUIDE
