"""V39-A: DOCX 경력기술서 5-col project table structure + candidate recall."""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf
import pytest

from app.ai.schemas.profile_candidate import (
    AnalysisMetaCandidate,
    EmploymentCandidate,
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
    SummaryCandidate,
)
from app.modules.analysis.docx_career_project_table import (
    CAREER_TABLE_MARKER,
    append_career_table_block,
    attach_docx_career_tables_to_pages,
    career_project_header_columns,
    extract_docx_career_project_rows_from_page,
    format_career_table_block,
    literal_quote_from_page,
    parse_career_period,
    parse_career_table_rows_from_text,
    supplement_docx_career_table_projects,
)
from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass
from app.modules.document_processing.types import ExtractedPage

_DOC = "a1111111-1111-1111-1111-111111111111"
_FONT = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

_PROJECTS = [
    (
        "출장 업무 스케줄 관리 애플리케이션",
        "2022.04~12",
        "개발",
        "스케줄 UI 개발",
        "내부",
    ),
    (
        "머신러닝 모델 성능 예측 애플리케이션",
        "2023.01~05",
        "개발",
        "모델 학습",
        "내부",
    ),
    (
        "모바일 퍼즐 게임",
        "2023.06~2024.02",
        "개발",
        "게임 로직",
        "내부",
    ),
    (
        "기존 델파이 기반 응용 소프트웨어를 AOS로 구현",
        "2024.02~04",
        "개발",
        "마이그레이션",
        "내부",
    ),
]


class FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def extract(self):
        return self._rows


class FakePage:
    def __init__(self, tables):
        self._tables = tables

    def find_tables(self, **kwargs):
        assert kwargs.get("strategy") == "lines"
        return SimpleNamespace(tables=self._tables)


def _candidate(
    *,
    projects: list[ProjectCandidate],
    employment: list[EmploymentCandidate] | None = None,
) -> ProfileCandidateDocument:
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스트"),
        projects=projects,
        employment_history=employment or [],
        education=[],
        certifications=[],
        jobs=[],
        skills=[],
        expertise=[],
        summary=SummaryCandidate(text=""),
        analysis=AnalysisMetaCandidate(overall_confidence=0.5),
    )


def test_header_requires_project_name_period_and_role_or_duty_or_customer() -> None:
    assert career_project_header_columns(
        ["사업명", "기간", "역할", "담당업무 상세", "발주처"]
    ) == {
        "project_name": 0,
        "period": 1,
        "role": 2,
        "duty": 3,
        "customer": 4,
    }
    # Ordinary resume / generic tables must not match.
    assert career_project_header_columns(["기간", "구분", "내용"]) == {}
    assert career_project_header_columns(["사업명", "기간"]) == {}
    assert career_project_header_columns(
        ["회사명", "기간", "직위", "담당업무"]
    ) == {}


def _original_p2_body() -> str:
    """Simulate DocumentPage text where titles/periods appear literally."""
    lines = ["경력 기술서", "사업명 기간 역할 담당업무 상세 발주처"]
    for name, period, role, duty, customer in _PROJECTS:
        lines.append(f"{name}")
        lines.append(f"{period} {role} {duty} {customer}")
    return "\n".join(lines)


def _page2_with_table_block() -> str:
    rows = [
        {
            "project_name": name,
            "period_raw": period,
            "role": role,
            "duty": duty,
            "customer_name": customer,
        }
        for name, period, role, duty, customer in _PROJECTS
    ]
    return append_career_table_block(_original_p2_body(), rows)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2022.04~12", ("2022.04", "2022.12")),
        ("2023.01~05", ("2023.01", "2023.05")),
        ("2023.06~2024.02", ("2023.06", "2024.02")),
        ("2024.02~04", ("2024.02", "2024.04")),
        ("2022 .04~12", ("2022.04", "2022.12")),
        ("bad", None),
    ],
)
def test_parse_career_period(raw: str, expected: tuple[str, str] | None) -> None:
    assert parse_career_period(raw) == expected


def test_extract_merges_multiline_cells_with_lines_strategy() -> None:
    table = FakeTable(
        [
            ["사업명", "기간", "역할", "담당업무 상세", "발주처"],
            [
                "출장 업무 스케줄 관리\n애플리케이션",
                "2022.04~12",
                "개발",
                "스케줄 UI",
                "내부",
            ],
            [
                "기존 델파이 기반 응용 소프트웨어를\nAOS로 구현",
                "2024.02~04",
                "개발",
                "마이그레이션",
                "내부",
            ],
        ]
    )
    rows = extract_docx_career_project_rows_from_page(FakePage([table]), page_no=2)
    assert len(rows) == 2
    assert rows[0]["project_name"] == "출장 업무 스케줄 관리 애플리케이션"
    assert rows[0]["start_date"] == "2022.04"
    assert rows[0]["end_date"] == "2022.12"
    assert rows[1]["project_name"] == "기존 델파이 기반 응용 소프트웨어를 AOS로 구현"
    assert rows[1]["start_date"] == "2024.02"
    assert rows[1]["end_date"] == "2024.04"


def test_append_block_preserves_original_page_text() -> None:
    original = "경력 기술서\n회사명 테스트\n앱 개발"
    rows = [
        {
            "project_name": name,
            "period_raw": period,
            "role": role,
            "duty": duty,
            "customer_name": customer,
        }
        for name, period, role, duty, customer in _PROJECTS
    ]
    merged = append_career_table_block(original, rows)
    assert merged.startswith(original)
    assert CAREER_TABLE_MARKER in merged
    assert "출장 업무 스케줄 관리 애플리케이션" in merged
    assert "기존 델파이 기반 응용 소프트웨어를 AOS로 구현" in merged
    # Idempotent.
    assert append_career_table_block(merged, rows) == merged


def test_no_table_docx_keeps_text_unchanged() -> None:
    original = "자유 형식 경력기술서\n프로젝트 서술만 있음"
    assert append_career_table_block(original, []) == original
    assert parse_career_table_rows_from_text(original, page_no=2) == []


def _sparse_ai_candidate() -> ProfileCandidateDocument:
    return _candidate(
        employment=[
            EmploymentCandidate(
                company_name="테스트소프트",
                start_date="2020.01",
                end_date="2024.12",
                responsibilities="앱 개발",
                source_refs=[
                    SourceRef(document_id=_DOC, page_no=1, quote_text="앱 개발")
                ],
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="앱 개발",
                start_date="2020.01",
                end_date="2024.12",
                customer_name="테스트소프트",
                source_refs=[
                    SourceRef(document_id=_DOC, page_no=1, quote_text="앱 개발")
                ],
            ),
            ProjectCandidate(
                project_name="모바일 퍼즐",  # truncated
                start_date="2023.06",
                end_date="2024.02",
                customer_name="내부",
                source_refs=[
                    SourceRef(document_id=_DOC, page_no=2, quote_text="모바일 퍼즐")
                ],
            ),
            ProjectCandidate(
                project_name="머신러닝 모델 성능 예측",
                start_date="2023.01",
                end_date="2023.05",
                customer_name="내부",
                source_refs=[
                    SourceRef(
                        document_id=_DOC,
                        page_no=2,
                        quote_text="머신러닝 모델 성능 예측",
                    )
                ],
            ),
        ],
    )


def _assert_four_real_projects(out: ProfileCandidateDocument, metrics: dict) -> None:
    assert metrics["table_rows"] == 4
    assert metrics["dropped_employment_fakes"] == 1
    assert metrics["supplemented_rows"] >= 2
    assert metrics["corrected_titles"] >= 1
    names = [p.project_name for p in out.projects]
    assert "앱 개발" not in names
    assert names.count("모바일 퍼즐 게임") == 1
    assert "출장 업무 스케줄 관리 애플리케이션" in names
    assert "머신러닝 모델 성능 예측 애플리케이션" in names
    assert "기존 델파이 기반 응용 소프트웨어를 AOS로 구현" in names
    assert len(out.projects) == 4
    assert len(out.employment_history) == 1
    original = _original_p2_body()
    for project in out.projects:
        p2_refs = [ref for ref in project.source_refs if ref.page_no == 2]
        assert p2_refs
        for ref in p2_refs:
            quote = ref.quote_text or ""
            assert "|" not in quote
            assert quote in original


def test_literal_quote_never_uses_pipe_rows() -> None:
    original = _original_p2_body()
    quote = literal_quote_from_page(
        original,
        title="모바일 퍼즐 게임",
        period_raw="2023.06~2024.02",
    )
    assert quote == "모바일 퍼즐 게임"
    assert quote in original
    assert "|" not in quote
    # Multiline title still returns an original contiguous slice.
    multiline = "출장 업무 스케줄 관리\n애플리케이션\n2022.04~12"
    quote2 = literal_quote_from_page(
        multiline,
        title="출장 업무 스케줄 관리 애플리케이션",
        period_raw="2022.04~12",
    )
    assert quote2 == "출장 업무 스케줄 관리\n애플리케이션"
    assert quote2 in multiline


def test_parse_rows_require_original_substring_quotes() -> None:
    # Pipe block alone (no original titles) → fail closed.
    block_only = format_career_table_block(
        [
            {
                "project_name": name,
                "period_raw": period,
                "role": role,
                "duty": duty,
                "customer_name": customer,
            }
            for name, period, role, duty, customer in _PROJECTS
        ]
    )
    assert parse_career_table_rows_from_text(block_only, page_no=2) == []
    rows = parse_career_table_rows_from_text(
        _page2_with_table_block(), page_no=2, document_id=_DOC
    )
    assert len(rows) == 4
    for row in rows:
        assert "|" not in row["quote_text"]
        assert row["quote_text"] in _original_p2_body()


@pytest.mark.parametrize("doc_type", ["DOC-CAREER", "DOC-RESUME"])
def test_supplement_recovers_four_projects_and_drops_employment_fake(
    doc_type: str,
) -> None:
    page1 = "근무경력\n회사명 테스트소프트\n기간 2020.01~2024.12\n담당업무 앱 개발"
    page2 = _page2_with_table_block()
    out, metrics = supplement_docx_career_table_projects(
        _sparse_ai_candidate(),
        page_texts={(_DOC, 1): page1, (_DOC, 2): page2},
        document_type_by_id={_DOC: doc_type},
        document_extension_by_id={_DOC: "docx"},
    )
    _assert_four_real_projects(out, metrics)


def test_second_pass_union_cannot_revive_employment_fake() -> None:
    """Table validation after second-pass merge drops reintroduced fakes."""
    page1 = "근무경력\n회사명 테스트소프트\n기간 2020.01~2024.12\n담당업무 앱 개발"
    page2 = _page2_with_table_block()
    first, _ = supplement_docx_career_table_projects(
        _sparse_ai_candidate(),
        page_texts={(_DOC, 1): page1, (_DOC, 2): page2},
        document_type_by_id={_DOC: "DOC-RESUME"},
        document_extension_by_id={_DOC: "docx"},
    )
    assert "앱 개발" not in [p.project_name for p in first.projects]

    second = _candidate(
        employment=list(first.employment_history),
        projects=list(first.projects)
        + [
            ProjectCandidate(
                project_name="앱 개발",
                start_date="2020.01",
                end_date="2024.12",
                customer_name="테스트소프트",
                source_refs=[
                    SourceRef(document_id=_DOC, page_no=1, quote_text="앱 개발")
                ],
            )
        ],
    )
    merged, _added = supplement_projects_from_second_pass(first, second)
    assert any(p.project_name == "앱 개발" for p in merged.projects)

    final, metrics = supplement_docx_career_table_projects(
        merged,
        page_texts={(_DOC, 1): page1, (_DOC, 2): page2},
        document_type_by_id={_DOC: "DOC-RESUME"},
        document_extension_by_id={_DOC: "docx"},
    )
    assert metrics["dropped_employment_fakes"] >= 1
    assert "앱 개발" not in [p.project_name for p in final.projects]
    assert len(final.projects) == 4
    assert len(final.employment_history) == 1


def test_resume_without_verified_project_table_is_noop() -> None:
    """DOC-RESUME without 사업명/기간… header must not invent projects."""
    page = "이력서\n학력\n학교명 기간 전공\nOO대학교 2010.03~2014.02 컴퓨터"
    candidate = _sparse_ai_candidate()
    out, metrics = supplement_docx_career_table_projects(
        candidate,
        page_texts={(_DOC, 1): page},
        document_type_by_id={_DOC: "DOC-RESUME"},
        document_extension_by_id={_DOC: "docx"},
    )
    assert metrics["table_rows"] == 0
    assert out.projects == candidate.projects


def test_non_career_or_non_docx_paths_untouched() -> None:
    page2 = _page2_with_table_block()
    candidate = _candidate(
        projects=[
            ProjectCandidate(
                project_name="앱 개발",
                start_date="2020.01",
                end_date="2024.12",
            )
        ],
        employment=[
            EmploymentCandidate(
                company_name="테스트",
                responsibilities="앱 개발",
                start_date="2020.01",
                end_date="2024.12",
            )
        ],
    )
    # KOSA / PDF must not consume the DOCX career block.
    out, metrics = supplement_docx_career_table_projects(
        candidate,
        page_texts={(_DOC, 2): page2},
        document_type_by_id={_DOC: "DOC-KOSA"},
        document_extension_by_id={_DOC: "pdf"},
    )
    assert metrics["table_rows"] == 0
    assert out.projects[0].project_name == "앱 개발"


def _build_career_pdf() -> bytes:
    """Synthetic ruled table; cell text via insert_text to avoid mid-glyph wrap."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_font(fontname="ko", fontfile=_FONT)
    p1.insert_text((40, 60), "근무경력", fontsize=12, fontname="ko")
    p1.insert_text((40, 90), "회사명 테스트소프트", fontsize=10, fontname="ko")
    p1.insert_text((40, 110), "담당업무 앱 개발", fontsize=10, fontname="ko")

    p2 = doc.new_page(width=595, height=842)
    p2.insert_font(fontname="ko", fontfile=_FONT)
    p2.insert_text((40, 40), "경력 기술서", fontsize=12, fontname="ko")
    headers = ["사업명", "기간", "역할", "담당업무 상세", "발주처"]
    # Keep titles single-line in the PDF fixture; multiline merge is covered
    # by the FakePage unit test above.
    data_rows = [list(row) for row in _PROJECTS]
    x0, y0 = 30, 70
    col_w = [220, 90, 40, 100, 50]
    header_h, row_h = 24, 36

    def _draw_row(y: float, cells: list[str], height: float) -> None:
        x = x0
        for i, cell in enumerate(cells):
            rect = pymupdf.Rect(x, y, x + col_w[i], y + height)
            p2.draw_rect(rect, width=0.6)
            p2.insert_text(
                (x + 3, y + 14),
                cell,
                fontsize=7,
                fontname="ko",
            )
            x += col_w[i]

    _draw_row(y0, headers, header_h)
    for i, row in enumerate(data_rows):
        _draw_row(y0 + header_h + i * row_h, row, row_h)

    pdf = doc.tobytes()
    doc.close()
    return pdf


def test_pdf_lines_strategy_extracts_four_projects_and_layout_attach() -> None:
    pdf_bytes = _build_career_pdf()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        rows = extract_docx_career_project_rows_from_page(doc.load_page(1), page_no=2)
    assert len(rows) == 4
    assert [(r["start_date"], r["end_date"]) for r in rows] == [
        ("2022.04", "2022.12"),
        ("2023.01", "2023.05"),
        ("2023.06", "2024.02"),
        ("2024.02", "2024.04"),
    ]
    joined = " ".join(r["project_name"] for r in rows)
    for name, *_rest in _PROJECTS:
        # Font shaping may tweak spaces; require core tokens survive.
        assert all(token in joined for token in name.split() if len(token) >= 2)

    pages = [
        ExtractedPage(
            page_no=1,
            extracted_text="근무경력\n앱 개발",
            extraction_method="TEXT_PARSER",
            layout_json=None,
        ),
        ExtractedPage(
            page_no=2,
            extracted_text="경력 기술서\n원본 텍스트 유지",
            extraction_method="TEXT_PARSER",
            layout_json={"needs_vlm": False},
        ),
    ]
    original_p2 = pages[1].extracted_text
    attach_docx_career_tables_to_pages(pdf_bytes, pages)
    assert pages[1].extracted_text == original_p2
    assert pages[1].layout_json is not None
    assert "docx_career_project_table" in pages[1].layout_json
    assert len(pages[1].layout_json["docx_career_project_table"]["rows"]) == 4
    assert not (pages[0].layout_json or {}).get("docx_career_project_table")
