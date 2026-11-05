"""Focused, LLM-free checks for the v25 read-only pilot's KOSA comparison."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from uuid import UUID

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate, SourceRef
from scripts.v25_kosa_candidate_readonly_pilot import _compare_kosa_rows


KOSA_ID = UUID("00000000-0000-0000-0000-000000000001")


def _row(title: str, customer: str, period: str = "2004.04.01 ~ 2005.12.30") -> str:
    return f"확인 | {title} | {period} | {customer} | 업체 | 개발 | SW개발"


def _project(title: str, customer: str, start: str, end: str) -> ProjectCandidate:
    return ProjectCandidate(
        project_name=title,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[SourceRef(document_id=str(KOSA_ID), page_no=3, quote_text="확인")],
    )


def _compare(source_row: str, projects: list[ProjectCandidate]) -> str:
    output = StringIO()
    with redirect_stdout(output):
        _compare_kosa_rows(source_row, ProfileCandidateDocument(projects=projects), KOSA_ID)
    return output.getvalue()


def test_v25_kosa_pilot_accepts_final_project_suffix_alias() -> None:
    candidate = _project("CRMS-05 프로젝트", "SKT", "2004.04", "2005.12")
    output = _compare(_row("CRMS-05", "SKT"), [candidate])
    assert "KOSA_ROW_01: PASS" in output
    assert "'NOT_FOUND': 0" in output


def test_v25_kosa_pilot_keeps_customer_conflicts_reviewable() -> None:
    for first_title, second_title, source_title in (
        ("그룹웨어 SM", "그룹웨어 SM", "그룹웨어 SM"),
        ("Dashboard 프로젝트", "Dashboard프로젝트", "Dashboard프로젝트"),
    ):
        projects = [
            _project(first_title, "발주사", "2004.04", "2005.12"),
            _project(second_title, "수행사", "2004.04.01", "2005.12.30"),
        ]
        output = _compare(_row(source_title, "발주사"), projects)
        assert "KOSA_ROW_01: NEEDS_REVIEW" in output
        assert "ambiguous_same_title_period" in output
        assert '"customer_name": "발주사"' in output
        assert '"customer_name": "수행사"' in output
        assert [project.customer_name for project in projects] == ["발주사", "수행사"]


def test_v25_kosa_pilot_ignores_same_title_from_different_period() -> None:
    projects = [
        _project("유지보수", "고객사", "2000.01", "2001.12"),
        _project("유지보수", "고객사", "2004.04", "2005.12"),
    ]
    output = _compare(_row("유지보수", "고객사"), projects)
    assert "KOSA_ROW_01: PASS" in output


def test_v25_kosa_pilot_never_promotes_fuzzy_title_or_wrong_period() -> None:
    wrong_title = _project("완전히 다른 사업", "고객사", "2004.04", "2005.12")
    output = _compare(_row("맞지 않는 제목", "고객사"), [wrong_title])
    assert "KOSA_ROW_01: NOT_FOUND" in output

    wrong_period = _project("맞는 제목", "고객사", "2006.04", "2007.12")
    output = _compare(_row("맞는 제목", "고객사"), [wrong_period])
    assert "KOSA_ROW_01: NEEDS_REVIEW" in output
    assert "no_matching_period" in output
