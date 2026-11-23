"""V40: real OOXML 5x5 source and guarded p1 employment mirror regression."""

from __future__ import annotations

import io
from uuid import UUID
import zipfile

import pymupdf

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
    attach_docx_career_tables_to_pages,
    extract_docx_career_project_rows_by_page,
    extract_docx_career_project_rows_from_docx,
    parse_career_table_rows_from_text,
    supplement_docx_career_table_projects,
)
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
)
from app.modules.document_processing.types import ExtractedPage
from app.storage.memory import MemoryObjectStorage

DOC = "a1111111-1111-1111-1111-111111111111"
OTHER = "b2222222-2222-2222-2222-222222222222"
ROWS = (
    ("출장 업무 스케줄 관리 애플리케이션", "2022.04~12", "개발", "UI 개발", "내부"),
    ("머신러닝 모델 성능 예측 애플리케이션", "2023.01~05", "개발", "모델 학습", "내부"),
    ("모바일 퍼즐 게임", "2023.06~2024.02", "개발", "게임 로직", "내부"),
    ("기존 델파이 기반 응용 소프트웨어를 AOS로 구현", "2024.02~04", "개발", "전환", "내부"),
)


def _docx() -> bytes:
    def cell(value: str) -> str:
        return '<w:tc><w:p><w:r><w:t>' + value + '</w:t></w:r></w:p></w:tc>'

    header = ("사업명", "기간", "역할", "담당업무 상세", "발주처")
    table = "".join(
        "<w:tr>" + "".join(cell(value) for value in row) + "</w:tr>"
        for row in (header, *ROWS)
    )
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:tbl>" + table + "</w:tbl></w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


def _pages():
    return [
        ExtractedPage(
            page_no=1,
            extracted_text="회사 테스트소프트\n부서 앱 개발\n2022.02~2024.04",
            extraction_method="TEXT_PARSER",
        ),
        ExtractedPage(
            page_no=2,
            extracted_text="경력 기술서\n" + "\n".join(
                f"{name}\n{period}" for name, period, *_ in ROWS
            ),
            extraction_method="TEXT_PARSER",
        ),
    ]


def _candidate(fake_refs: list[SourceRef] | None = None):
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스트"),
        employment_history=[
            EmploymentCandidate(
                company_name="테스트소프트",
                department="앱 개발",
                responsibilities=None,
                start_date="2022.02",
                end_date="2024.04",
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="앱 개발",
                start_date="2022.02",
                end_date="2024.04",
                source_refs=fake_refs if fake_refs is not None else [
                    SourceRef(document_id=DOC, page_no=1, quote_text="앱 개발")
                ],
            ),
            ProjectCandidate(
                project_name=ROWS[0][0],
                start_date="2022.04",
                end_date="2022.12",
                source_refs=[SourceRef(document_id=DOC, page_no=2, quote_text=ROWS[0][0])],
            ),
            ProjectCandidate(
                project_name=ROWS[1][0],
                start_date="2023.01",
                end_date="2023.05",
                source_refs=[SourceRef(document_id=DOC, page_no=2, quote_text=ROWS[1][0])],
            ),
        ],
        education=[],
        certifications=[],
        jobs=[],
        skills=[],
        expertise=[],
        summary=SummaryCandidate(text=""),
        analysis=AnalysisMetaCandidate(overall_confidence=0.5),
    )


def _prompt_rows(pages):
    out = {}
    for page in pages:
        if (page.layout_json or {}).get("docx_career_project_table"):
            from app.modules.analysis.docx_career_project_table import (
                append_career_table_block, rows_from_layout
            )
            out[(DOC, page.page_no)] = append_career_table_block(
                page.extracted_text, rows_from_layout(page.layout_json)
            )
        else:
            out[(DOC, page.page_no)] = page.extracted_text
    return out


def test_real_shape_native_docx_rows_and_layout_fallback() -> None:
    native = extract_docx_career_project_rows_from_docx(_docx())
    assert len(native) == 4
    assert [row["project_name"] for row in native] == [r[0] for r in ROWS]
    pages = _pages()
    by_page = extract_docx_career_project_rows_by_page(_docx(), pages)
    assert list(by_page) == [2]
    assert len(by_page[2]) == 4
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        preview = pdf.tobytes()
    attach_docx_career_tables_to_pages(preview, pages, docx_bytes=_docx())
    assert len(pages[1].layout_json["docx_career_project_table"]["rows"]) == 4
    assert pages[0].layout_json is None
    assert "경력 기술서" in pages[1].extracted_text


def test_resume_replay_corrects_four_and_drops_department_mirror() -> None:
    pages = _pages()
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        preview = pdf.tobytes()
    storage = MemoryObjectStorage()
    storage.put_bytes("original", _docx())
    storage.put_bytes("preview", preview)
    builder = AnalysisSourceBuilder(storage, vlm=None)
    doc = DocumentSnapshot(
        id=UUID(DOC), original_filename="synthetic.docx",
        extension="docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key="original", preview_storage_key="preview",
        document_type_code="DOC-RESUME", document_type_name="이력서",
        version_no=1, person_id=UUID(OTHER),
        pages=[
            PageSnapshot(page_no=p.page_no, extracted_text=p.extracted_text,
                         layout_json=p.layout_json, extraction_method=p.extraction_method)
            for p in pages
        ],
    )
    source = builder.build([doc])
    text = source.blocks[0].text
    assert "[CAREER_PROJECT_TABLE]" in text
    # Match the same original page body that goes to the analysis prompt.
    prompt = source.build_prompt_source(40000)
    rows = parse_career_table_rows_from_text(prompt.page_texts[(DOC, 2)],
                                              page_no=2, document_id=DOC)
    assert len(rows) == 4
    output, metrics = supplement_docx_career_table_projects(
        _candidate(), page_texts=prompt.page_texts,
        document_type_by_id={DOC:"DOC-RESUME"},
        document_extension_by_id={DOC:"docx"},
    )
    assert len(output.projects) == 4
    assert metrics["table_rows"] == 4
    assert metrics["dropped_employment_fakes"] == 1
    assert all(p.project_name != "앱 개발" for p in output.projects)
    assert len(output.employment_history) == 1


def test_same_department_real_project_with_p2_or_other_doc_evidence_preserved() -> None:
    pages = _pages()
    attach_docx_career_tables_to_pages(b"not-pdf", pages, docx_bytes=_docx())
    prompt_rows = _prompt_rows(pages)
    for refs in (
        [SourceRef(document_id=DOC, page_no=2, quote_text="앱 개발")],
        [SourceRef(document_id=OTHER, page_no=1, quote_text="앱 개발")],
        [],
    ):
        output, metrics = supplement_docx_career_table_projects(
            _candidate(fake_refs=refs),
            page_texts=prompt_rows,
            document_type_by_id={DOC:"DOC-RESUME"},
            document_extension_by_id={DOC:"docx"},
        )
        assert len(output.projects) == 5
        assert metrics["dropped_employment_fakes"] == 0


def test_missing_or_invalid_native_docx_no_rows() -> None:
    assert extract_docx_career_project_rows_from_docx(b"invalid") == []
