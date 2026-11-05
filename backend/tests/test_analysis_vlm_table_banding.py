"""Focused, DB-free regression for opt-in scanned-table row-banded VLM."""

from __future__ import annotations

from uuid import uuid4

import pymupdf
import pytest

from app.ai.providers.vlm import FakeVLMProvider
from app.core.config import Settings
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
)
from app.modules.analysis.vlm_table_banding import (
    detect_table_row_bands,
    is_valid_table_band_transcription,
    normalize_table_band_transcription,
)


def _pdf_bytes(*, ruled: bool = True) -> bytes:
    document = pymupdf.open()
    try:
        page = document.new_page(width=595, height=842)
        if ruled:
            for y in range(100, 701, 40):
                page.draw_line(
                    (70, y), (525, y), color=(0.35, 0.35, 0.35), width=0.8
                )
            for x in (70, 115, 260, 352, 425, 525):
                page.draw_line(
                    (x, 100), (x, 700), color=(0.3, 0.3, 0.3), width=0.8
                )
            for y in range(110, 690, 40):
                page.insert_text((122, y + 10), "PROJECT 2025.01", fontsize=11)
        else:
            page.insert_text((100, 100), "Plain paragraph without a table")
        return document.tobytes()
    finally:
        document.close()


def _build(pdf: bytes, vlm: FakeVLMProvider, *, flag: bool, **options):
    params = {
        "analysis_vlm_table_banding_enabled": flag,
        "analysis_vlm_table_banding_rows_per_band": 5,
        "analysis_vlm_table_banding_max_calls_per_page": 6,
        "analysis_vlm_table_banding_max_calls_total": 16,
    }
    params.update(options)
    settings = Settings(_env_file=None, **params)
    source = AnalysisSourceBuilder(object(), settings=settings, vlm=vlm)
    source._open_render_source = (  # type: ignore[method-assign]
        lambda _snapshot: pymupdf.open(stream=pdf, filetype="pdf")
    )
    person_id = uuid4()
    document = DocumentSnapshot(
        id=uuid4(),
        original_filename="table.pdf",
        extension="pdf",
        mime_type="application/pdf",
        storage_key="unused",
        preview_storage_key=None,
        document_type_code=None,
        document_type_name=None,
        version_no=1,
        person_id=person_id,
        pages=[
            PageSnapshot(
                page_no=1,
                extracted_text=None,
                layout_json={"needs_vlm": True},
                extraction_method="TEXT_PARSER",
            )
        ],
    )
    return source.build([document])


def test_ruled_table_uses_real_row_boundaries():
    document = pymupdf.open(stream=_pdf_bytes(), filetype="pdf")
    try:
        bands = detect_table_row_bands(document[0], dpi=200, rows_per_band=5)
    finally:
        document.close()
    assert [(band.first_row, band.last_row) for band in bands] == [
        (1, 5), (6, 10), (11, 15)
    ]


def test_partial_width_grid_detected_without_fixed_crop_coordinates():
    document = pymupdf.open()
    try:
        page = document.new_page(width=595, height=842)
        for y in range(100, 701, 40):
            page.draw_line((175, y), (450, y), color=(0.35, 0.35, 0.35), width=0.8)
        for x in (175, 240, 310, 385, 450):
            page.draw_line((x, 100), (x, 700), color=(0.3, 0.3, 0.3), width=0.8)
        for y in range(110, 690, 40):
            page.insert_text((244, y + 10), "ROW", fontsize=10)
        bands = detect_table_row_bands(page, dpi=180)
        assert [(band.first_row, band.last_row) for band in bands] == [
            (1, 5), (6, 10), (11, 15)
        ]
    finally:
        document.close()


def test_footer_barcode_block_is_not_detected_as_table_rows():
    document = pymupdf.open()
    try:
        page = document.new_page(width=595, height=842)
        for x in (70, 225, 380):
            page.draw_rect(
                pymupdf.Rect(x, 625, x + 145, 715),
                color=(0.1, 0.1, 0.1),
                fill=(0.2, 0.2, 0.2),
            )
        assert detect_table_row_bands(page, dpi=180) == []
    finally:
        document.close()


def test_unruled_page_rejected_and_strict_row_count():
    document = pymupdf.open(stream=_pdf_bytes(ruled=False), filetype="pdf")
    try:
        assert not detect_table_row_bands(document[0], dpi=200)
    finally:
        document.close()
    assert is_valid_table_band_transcription("A|B|C\nD|E|F", expected_rows=2)
    assert not is_valid_table_band_transcription("A|B|C", expected_rows=2)
    assert not is_valid_table_band_transcription("A|B\nC|D|E", expected_rows=2)


def test_repeated_trailing_gt_is_one_cell_not_a_column():
    raw = (
        "확인 | 사업A | 2025 | 발주처 | 소속사 | 차장 | SW개발 | > 응용SW개발\n"
        "확인 | 사업B | 2024 | 발주처 | 소속사 | 과장 | IT컨설팅 | > 정보기술컨설팅"
    )
    normalized = normalize_table_band_transcription(raw)
    assert all(len(row.split("|")) == 7 for row in normalized.splitlines())
    assert "SW개발 > 응용SW개발" in normalized
    assert "IT컨설팅 > 정보기술컨설팅" in normalized
    assert is_valid_table_band_transcription(
        raw, expected_rows=2, expected_columns=7
    )
    assert not is_valid_table_band_transcription(
        raw, expected_rows=2, expected_columns=8
    )



def test_repeated_merged_confirmation_cell_restores_seven_columns():
    raw = (
        "확인 외국인 EIS 추가개발 프로젝트 | 2012.12.03 ~ 2012.12.31"
        " | 한국고용 정보원 | 소속사 | 수석 | SW개발 > 응용SW개발\n"
        "확인 아리따움 POS 재구축 프로젝트 OLAP개발 | 2011.02.01 ~ 2011.06.30"
        " | 아모레퍼 시픽 | 소속사 | 과장 | SW개발 > 응용SW개발"
    )
    cleaned = normalize_table_band_transcription(raw)
    rows = [[cell.strip() for cell in line.split("|")] for line in cleaned.splitlines()]
    assert len(rows) == 2
    assert all(len(cells) == 7 and cells[0] == "확인" for cells in rows)
    assert rows[0][1] == "외국인 EIS 추가개발 프로젝트"
    assert rows[1][1] == "아리따움 POS 재구축 프로젝트 OLAP개발"
    assert rows[1][3] == "아모레퍼 시픽"
    assert is_valid_table_band_transcription(
        raw, expected_rows=2, expected_columns=7
    )
    assert not is_valid_table_band_transcription(
        raw, expected_rows=2, expected_columns=6
    )
    mixed = raw.splitlines()[0] + "\n" + raw.splitlines()[1].replace(
        "확인 아리따움", "미확인 아리따움"
    )
    assert not is_valid_table_band_transcription(
        mixed, expected_rows=2, expected_columns=7
    )


def test_enabled_pipeline_restores_repeated_merged_confirmation_cells():
    rows = "\n".join(
        f"확인 프로젝트{i} | 2025.01 | 발주처 | 소속사 | 차장 | SW개발 > 응용SW개발"
        for i in range(5)
    )
    result = _build(_pdf_bytes(), FakeVLMProvider(rows), flag=True)
    assert len(result.vlm_transcriptions) == 1
    transcription = result.vlm_transcriptions[0]
    assert transcription.layout_json.get("vlm_table_banded") is True
    cleaned = transcription.persisted_text.splitlines()
    assert len(cleaned) == 15
    assert all(
        len(line.split("|")) == 7 and line.startswith("확인 | 프로젝트")
        for line in cleaned
    )

def test_enabled_pipeline_persists_normalized_seven_column_rows():
    raw_rows = "\n".join(
        f"확인 | 사업{i} | 2025 | 발주처 | 소속사 | 차장 | SW개발 | > 응용SW개발"
        for i in range(5)
    )
    result = _build(_pdf_bytes(), FakeVLMProvider(raw_rows), flag=True)
    assert result.total_vlm_pages == 1
    assert len(result.vlm_transcriptions) == 1
    cleaned_rows = result.vlm_transcriptions[0].persisted_text.splitlines()
    assert len(cleaned_rows) == 15
    assert all(len(row.split("|")) == 7 for row in cleaned_rows)


@pytest.mark.parametrize("flag,expected_calls", [(False, 1), (True, 3)])
def test_flag_off_preserves_v1_and_flag_on_bands(flag, expected_calls):
    lines = "\n".join(f"확인 | 프로젝트{i} | 2025.01" for i in range(5))
    vlm = FakeVLMProvider(lines)
    result = _build(_pdf_bytes(), vlm, flag=flag)
    assert vlm.calls == expected_calls
    assert result.total_vlm_pages == 1
    assert len(result.vlm_transcriptions) == 1
    item = result.vlm_transcriptions[0]
    assert item.extraction_method == "VLM"
    assert item.layout_json.get("vlm_table_banded", False) is flag
    assert result.blocks[0].vlm_table_band_calls_used == (3 if flag else 0)


def test_inconsistent_band_output_falls_back_to_full_page():
    vlm = FakeVLMProvider("not a row-by-row table")
    result = _build(_pdf_bytes(), vlm, flag=True)
    assert vlm.calls == 2  # first rejected band, then unchanged v1 path
    assert len(result.vlm_transcriptions) == 1
    item = result.vlm_transcriptions[0]
    assert item.persisted_text == "not a row-by-row table"
    assert "vlm_table_banded" not in item.layout_json
    assert result.blocks[0].vlm_table_band_calls_used == 1


def test_not_enough_band_call_budget_falls_back_without_partial_calls():
    vlm = FakeVLMProvider("original page")
    result = _build(
        _pdf_bytes(),
        vlm,
        flag=True,
        analysis_vlm_table_banding_max_calls_total=2,
    )
    assert vlm.calls == 1
    assert result.blocks[0].vlm_table_band_calls_used == 0


def test_already_transcribed_page_not_overwritten_when_flag_on():
    vlm = FakeVLMProvider("should not be called")
    settings = Settings(_env_file=None, analysis_vlm_table_banding_enabled=True)
    source = AnalysisSourceBuilder(object(), settings=settings, vlm=vlm)
    document = DocumentSnapshot(
        id=uuid4(),
        original_filename="already.pdf",
        extension="pdf",
        mime_type="application/pdf",
        storage_key="unused",
        preview_storage_key=None,
        document_type_code=None,
        document_type_name=None,
        version_no=1,
        person_id=uuid4(),
        pages=[
            PageSnapshot(
                page_no=1,
                extracted_text="existing confirmed transcription",
                layout_json={"needs_vlm": False, "vlm_transcribed": True},
                extraction_method="VLM",
            )
        ],
    )
    result = source.build([document])
    assert vlm.calls == 0
    assert result.blocks[0].text == "[PAGE 1]\nexisting confirmed transcription"
    assert result.vlm_transcriptions == []


@pytest.mark.parametrize("valid", [True, False])
def test_opt_in_reprocesses_existing_vlm_table_in_memory_only(valid):
    prior = "stored VLM transcription must stay unchanged"
    rows = "\n".join(
        f"확인 | 프로젝트{i} | 2025.01 | 발주처 | 소속사 | 개발 | SW개발"
        for i in range(5)
    )
    vlm = FakeVLMProvider(rows if valid else "not a table row")
    settings = Settings(
        _env_file=None,
        analysis_vlm_table_banding_enabled=True,
        analysis_vlm_table_banding_reprocess_done=True,
    )
    source = AnalysisSourceBuilder(object(), settings=settings, vlm=vlm)
    source._open_render_source = (  # type: ignore[method-assign]
        lambda _snapshot: pymupdf.open(stream=_pdf_bytes(), filetype="pdf")
    )
    page = PageSnapshot(
        page_no=1,
        extracted_text=prior,
        layout_json={"needs_vlm": False, "vlm_transcribed": True},
        extraction_method="VLM",
    )
    document = DocumentSnapshot(
        id=uuid4(),
        original_filename="existing-kosa.pdf",
        extension="pdf",
        mime_type="application/pdf",
        storage_key="unused",
        preview_storage_key=None,
        document_type_code=None,
        document_type_name=None,
        version_no=1,
        person_id=uuid4(),
        pages=[page],
    )

    result = source.build([document])

    assert result.vlm_transcriptions == []
    assert page.extracted_text == prior
    assert page.layout_json == {"needs_vlm": False, "vlm_transcribed": True}
    if valid:
        assert vlm.calls == 3
        assert result.blocks[0].text.startswith("[PAGE 1]\n확인 | 프로젝트0")
        assert result.blocks[0].vlm_pages_used == 1
        assert result.blocks[0].vlm_table_band_calls_used == 3
    else:
        assert vlm.calls == 1
        assert result.blocks[0].text == f"[PAGE 1]\n{prior}"
        assert result.blocks[0].vlm_pages_used == 0
