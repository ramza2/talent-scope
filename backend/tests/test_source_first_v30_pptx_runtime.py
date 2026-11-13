"""Strict v30 PPTX OOXML source tests. No database, model, or remote calls."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.pptx_native_source_v30 import (
    _is_full_width_trailing_note,
    _is_merged_secondary_header,
    _is_verified_trailing_footer,
    pptx_native_table_rows,
)
from app.modules.analysis.project_row_match_v30 import build_clusters
from app.modules.analysis.source_first_v30_runtime import assemble_live_source_first


def _table_xml(body, *, frame=True):
    rows = [
        ["수행기간", "프로젝트명", "담당역할", "고객사"],
        *body,
    ]
    records = []
    for fields in rows:
        cells = "".join(
            f"<a:tc><a:txBody><a:p><a:r><a:t>{escape(str(value))}</a:t>"
            "</a:r></a:p></a:txBody></a:tc>"
            for value in fields
        )
        records.append(f'<a:tr h="508000">{cells}</a:tr>')
    xfrm = (
        '<p:xfrm><a:off x="127000" y="254000"/>'
        '<a:ext cx="9144000" cy="3048000"/></p:xfrm>'
    ) if frame else ""
    return (
        "<p:graphicFrame>" + xfrm
        + "<a:graphic><a:graphicData><a:tbl>"
        + "<a:tblGrid><a:gridCol w=\"2286000\"/></a:tblGrid>"
        + "".join(records)
        + "</a:tbl></a:graphicData></a:graphic>"
        + "</p:graphicFrame>"
    )


def _make_pptx(*, bad_period=False, frame=True, blank_row=False, extra_rows=None):
    projects = [
        ["2020.02~2020.04", "비공개 플랫폼 구축사업", "PL", "비공개 고객기관"]
    ]
    if bad_period:
        projects.append(["", "비공개 별도 사업", "PM", "비공개 고객기관"])
    if blank_row:
        projects.append(["", "", "", ""])
    if extra_rows:
        projects.extend(extra_rows)
    xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree>' + _table_xml(projects, frame=frame)
        + '</p:spTree></p:cSld></p:sld>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ppt/slides/slide1.xml", xml)
    return buf.getvalue()


class FakeStorage:
    def __init__(self, items):
        self.items = items
        self.closed = []

    def get(self, key):
        data = self.items[key]
        state = SimpleNamespace(closed=False)

        def chunks():
            midpoint = max(1, len(data) // 2)
            yield data[:midpoint]
            yield data[midpoint:]

        def close():
            state.closed = True
            self.closed.append(key)

        return SimpleNamespace(iter_chunks=chunks, close=close)


def _doc(name):
    return SimpleNamespace(
        id=name,
        extension="pptx",
        storage_key=name,
        pages=[SimpleNamespace(page_no=1)],
    )


def _builder(data, *, second=False):
    items = {"doc1": data}
    if second:
        items["doc2"] = data
    storage = FakeStorage(items)
    builder = SimpleNamespace(storage=storage)
    return builder, storage


def test_original_pptx_rows_use_own_slide_geometry():
    builder, storage = _builder(_make_pptx(blank_row=True))
    rows = pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "pptx-native-table"
    assert row["geometry_space"] == "pptx-slide-points"
    assert row["column_mapping_verified"] is True
    assert row["fields"]["project_name"] == "비공개 플랫폼 구축사업"
    assert row["fields"]["period"] == "2020.02~2020.04"
    assert row["bbox"][2] > row["bbox"][0]
    assert row["bbox"][3] > row["bbox"][1]
    assert storage.closed == ["doc1"]


def test_two_exact_pptx_rows_merge_only_on_verified_identity():
    builder, storage = _builder(_make_pptx(), second=True)

    class NativeBuilder:
        def __init__(self, backend):
            self.storage = backend.storage

        def _open_render_source(self, _doc):
            raise AssertionError("PPTX original must be preferred to PDF preview")

    result = assemble_live_source_first(
        source_builder=NativeBuilder(builder),
        documents=[_doc("doc1"), _doc("doc2")],
        original_candidate=ProfileCandidateDocument.model_validate(
            {"profile": {"name": "시험사용자"}, "projects": []}
        ),
        confirmed_snapshot={"projects": []},
        page_texts={},
    )

    assert result.diagnostics["native_pdf_input_rows"] == 2
    assert result.diagnostics["candidate_project_count"] == 1
    assert result.diagnostics["source_status_counts"] == {
        "verified_exact_multi_document": 1
    }
    assert result.candidate.projects[0].project_name == "비공개 플랫폼 구축사업"
    assert len(result.private_source_provenance[0]["native_pdf_row_sources"]) == 2
    assert sorted(storage.closed) == ["doc1", "doc2"]


def test_yearless_nonempty_row_fails_closed():
    builder, storage = _builder(_make_pptx(bad_period=True))
    with pytest.raises(ValueError, match="V30_PPTX_UNVERIFIED_PROJECT_ROW"):
        pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))
    assert storage.closed == ["doc1"]


def test_unreliable_table_geometry_fails_closed():
    builder, _ = _builder(_make_pptx(frame=False))
    with pytest.raises(ValueError, match="V30_PPTX_TABLE_GEOMETRY_MISSING"):
        pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))


def test_slide_page_mismatch_fails_closed():
    builder, _ = _builder(_make_pptx())
    document = _doc("doc1")
    document.pages.append(SimpleNamespace(page_no=2))
    with pytest.raises(ValueError, match="V30_PPTX_SLIDE_PAGE_MISMATCH"):
        pptx_native_table_rows(source_builder=builder, document=document)


def test_corrupted_archive_fails_closed():
    builder, _ = _builder(b"not-a-pptx")
    with pytest.raises(ValueError, match="V30_PPTX_INVALID_ARCHIVE"):
        pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))


def test_pptx_row_requires_geometry_source_marker():
    builder, _ = _builder(_make_pptx())
    row = pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))[0]
    row.pop("geometry_space")
    report = build_clusters([row])
    assert report["status_counts"] == {"unverified_source_row": 1}


def test_non_date_period_only_section_label_is_skipped():
    builder, _ = _builder(_make_pptx(extra_rows=[
        ["프로젝트 수행 경력", "", "", ""],
        ["", "", "", ""],
    ]))
    result = pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))
    assert len(result) == 1
    assert result[0]["fields"]["project_name"] == "비공개 플랫폼 구축사업"


@pytest.mark.parametrize("cells", [
    ["2024", "", "", ""],                        # Orphan date: not a separator
    ["", "비공개 추가 프로젝트", "", ""],         # Missing period, possible project
    ["", "", "프로젝트 상세 업무", ""],           # Continuation cells
    ["", "", "", "비공개 고객기관"],              # Customer-only row
    ["", "", "", "", "다른 열의 보조 정보"],       # Unmapped column: ambiguous
])
def test_incomplete_nonempty_rows_remain_fail_closed(cells):
    builder, storage = _builder(_make_pptx(extra_rows=[cells]))
    with pytest.raises(ValueError, match="V30_PPTX_UNVERIFIED_PROJECT_ROW"):
        pptx_native_table_rows(source_builder=builder, document=_doc("doc1"))
    assert storage.closed == ["doc1"]


def test_explicit_trailing_summary_in_unmapped_first_cell():
    cols = {"period": 1, "project_name": 3, "role": 4, "customer": 7}
    row = ["합계: 33건", "", "", "", "", "", "", "", ""]
    assert _is_verified_trailing_footer(
        row, cols, row_index=23, table_row_count=24,
    )
    assert not _is_verified_trailing_footer(
        row, cols, row_index=22, table_row_count=24,
    )


@pytest.mark.parametrize("label", [
    "추가 프로젝트", "비공개인력명", "2024년 이력", "계속되는 내용", "현장 경험",
])
def test_ambiguous_trailing_text_in_unmapped_column_fails_closed(label):
    cols = {"period": 1, "project_name": 3, "role": 4, "customer": 7}
    cells = [label, "", "", "", "", "", "", "", ""]
    assert not _is_verified_trailing_footer(
        cells, cols, row_index=23, table_row_count=24,
    )


def test_footer_rejected_if_another_cell_has_content():
    cols = {"period": 1, "project_name": 3, "role": 4, "customer": 7}
    assert not _is_verified_trailing_footer(
        ["합계: 33건", "", "", "비공개 프로젝트", "", "", "", "", ""],
        cols, row_index=23, table_row_count=24,
    )


def _row_with_merge_flags(*, vertically_merged=(), full_width=False, ncols=9):
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    tr = ET.Element(f"{{{a_ns}}}tr", {"h": "508000"})
    for i in range(ncols):
        attrs = {}
        if full_width:
            attrs = {"gridSpan": str(ncols)} if i == 0 else {"hMerge": "1"}
        elif i in vertically_merged:
            attrs = {"vMerge": "1"}
        ET.SubElement(tr, f"{{{a_ns}}}tc", attrs)
    return tr


def test_merged_secondary_header_only_immediately_after_header():
    columns = {"period": 1, "project_name": 3, "role": 4, "customer": 7}
    values = ["", "실적 분류", "", "", "", "보조 제목", "보조 열", "", ""]
    row = _row_with_merge_flags(vertically_merged=(0, 2, 3, 4, 7, 8))
    assert _is_merged_secondary_header(
        values, columns, row, row_index=2, first_data=2,
    )
    assert not _is_merged_secondary_header(
        values, columns, row, row_index=3, first_data=2,
    )
    assert not _is_merged_secondary_header(
        values[:3] + ["비공개 프로젝트"] + values[4:],
        columns, row, row_index=2, first_data=2,
    )


def test_secondary_header_cannot_skip_date_or_without_merged_project_cells():
    columns = {"period": 0, "project_name": 2, "role": 4, "customer": 7}
    row = _row_with_merge_flags(vertically_merged=(1, 2, 3, 6, 7))
    values = ["실적 구분", "", "", "", "", "보조", "", "", ""]
    assert _is_merged_secondary_header(
        values, columns, row, row_index=2, first_data=2,
    )
    assert not _is_merged_secondary_header(
        ["2024년"] + values[1:], columns, row,
        row_index=2, first_data=2,
    )
    assert not _is_merged_secondary_header(
        values, columns, _row_with_merge_flags(vertically_merged=(1, 3, 6)),
        row_index=2, first_data=2,
    )


def test_full_width_note_requires_complete_merge_and_final_position():
    columns = {"period": 1, "project_name": 3, "role": 4, "customer": 7}
    values = ["작성 기준 참고", "", "", "", "", "", "", "", ""]
    merged = _row_with_merge_flags(full_width=True)
    assert _is_full_width_trailing_note(
        values, columns, merged, row_index=23, table_row_count=24,
    )
    assert not _is_full_width_trailing_note(
        values, columns, merged, row_index=22, table_row_count=24,
    )
    assert not _is_full_width_trailing_note(
        values, columns, _row_with_merge_flags(),
        row_index=23, table_row_count=24,
    )


@pytest.mark.parametrize("text", [
    "2024년 프로젝트", "비공개 프로젝트", "고객사 현황", "수행기간 2020",
])
def test_full_width_project_like_text_never_skipped(text):
    columns = {"period": 1, "project_name": 3, "customer": 7}
    merged = _row_with_merge_flags(full_width=True)
    assert not _is_full_width_trailing_note(
        [text] + [""] * 8, columns, merged,
        row_index=23, table_row_count=24,
    )
