"""V38 scanned KOSA quality fallback: rule-free image tiles, DB-free tests."""

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
    is_kosa_empty_markdown_table_vlm_quality_issue,
    is_plausible_kosa_employment_recovery,
)


def _empty_kosa_page() -> str:
    return "소프트웨어기술자 경력증명서\n회사명\n" + (
        "|  |  |  |  |  |\n" * 420
    )


def _scan_pdf() -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 70), "Scanned image, no table rules")
        return doc.tobytes()
    finally:
        doc.close()


def _source(vlm, *, call_cap: int = 16) -> tuple[AnalysisSourceBuilder, PageSnapshot, DocumentSnapshot]:
    prior = _empty_kosa_page()
    page = PageSnapshot(
        page_no=1,
        extracted_text=prior,
        extraction_method="VLM",
        layout_json={"needs_vlm": False, "vlm_transcribed": True},
    )
    doc = DocumentSnapshot(
        id=uuid4(),
        original_filename="kosa-scanned-p1.pdf",
        extension="pdf",
        mime_type="application/pdf",
        storage_key="unused",
        preview_storage_key=None,
        document_type_code="DOC-KOSA",
        document_type_name="KOSA",
        version_no=1,
        person_id=uuid4(),
        pages=[page],
    )
    settings = Settings(
        _env_file=None,
        analysis_vlm_table_banding_enabled=True,
        analysis_vlm_table_banding_reprocess_done=False,
        analysis_vlm_table_banding_max_calls_total=call_cap,
    )
    source = AnalysisSourceBuilder(object(), settings=settings, vlm=vlm)
    source._open_render_source = (  # type: ignore[method-assign]
        lambda _snapshot: pymupdf.open(stream=_scan_pdf(), filetype="pdf")
    )
    return source, page, doc


class TileVLM:
    def __init__(self, quality_ok: bool) -> None:
        self.calls = 0
        self.contexts: list[dict] = []
        self.quality_ok = quality_ok

    def transcribe_image(
        self, *, image_bytes, mime_type="image/png", log_context=None, user_prompt=None
    ) -> str:
        assert image_bytes and mime_type == "image/png"
        assert user_prompt and "근무경력" in user_prompt
        self.calls += 1
        context = log_context or {}
        self.contexts.append(context)
        if context.get("kosa_work_table_crop"):
            if self.quality_ok:
                return "\n".join(
                    f"가상회사{i} | 200{i}.01.01 ~ 200{i}.12.31 | 개발"
                    for i in range(1, 8)
                )
            return "프로젝트 참여기간 | 2024.01.01 ~ 2024.06.01"
        if context.get("page_tile"):
            if self.quality_ok:
                return [
                    "근무경력\n회사명 | 재직기간\n에이회사 | 2001.01.01 ~ 2003.02.01",
                    "비이회사 | 2003.03.01 ~ 2005.06.01",
                    "씨이회사 | 2005.07.01 ~ 2008.12.31",
                ][int(context["page_tile"].split("/")[0]) - 1]
            return "추출할 수 없음"
        raise AssertionError("Unexpected VLM call")


def test_scanned_page_recovery_quality_gate() -> None:
    assert is_kosa_empty_markdown_table_vlm_quality_issue(_empty_kosa_page())
    assert is_plausible_kosa_employment_recovery(
        "근무경력\n에이회사 | 2001.01.01 ~ 2003.02.01\n"
        "비이회사 | 2003.03.01 ~ 2005.06.01"
    )
    assert not is_plausible_kosa_employment_recovery("회사명 | 1962.06.24")
    assert not is_plausible_kosa_employment_recovery(
        "기술경력\n2001.01.01 ~ 2003.02.01\n2003.03.01 ~ 2005.06.01"
    )
    assert not is_plausible_kosa_employment_recovery(_empty_kosa_page())


@pytest.mark.parametrize("quality_ok", [True, False])
def test_scanned_kosa_without_ruling_recovered_in_memory_only(quality_ok) -> None:
    vlm = TileVLM(quality_ok)
    source, page, doc = _source(vlm)
    prior = page.extracted_text
    layout = dict(page.layout_json or {})

    result = source.build([doc])

    assert result.vlm_transcriptions == []
    assert page.extracted_text == prior
    assert page.layout_json == layout
    assert vlm.contexts[0].get("kosa_work_table_crop") is True
    if quality_ok:
        assert vlm.calls == 1
        assert result.blocks[0].text.startswith("[PAGE 1]\n근무경력\n")
        assert "가상회사1 | 2001.01.01" in result.blocks[0].text
        assert "가상회사7 | 2007.01.01" in result.blocks[0].text
        assert result.blocks[0].vlm_pages_used == 1
        assert result.blocks[0].vlm_table_band_calls_used == 1
    else:
        # Bad one-crop evidence causes a conservative fallback (three tiles).
        assert vlm.calls == 4
        assert [ctx.get("page_tile") for ctx in vlm.contexts[1:]] == [
            "1/3", "2/3", "3/3"
        ]
        assert result.blocks[0].text == f"[PAGE 1]\n{prior.strip()}"
        assert result.blocks[0].vlm_pages_used == 0


def test_scanned_kosa_tiles_respect_total_call_cap() -> None:
    vlm = FakeVLMProvider("근무경력\n에이회사 2001.01 ~ 2002.12")
    source, page, doc = _source(vlm, call_cap=2)

    result = source.build([doc])

    assert vlm.calls == 1  # Crop consumes one call; three-tile fallback stays capped.
    assert result.vlm_transcriptions == []
    assert result.blocks[0].text == f"[PAGE 1]\n{page.extracted_text.strip()}"



def test_kosa_work_crop_rejects_invalid_dates_and_project_rows() -> None:
    from app.modules.analysis.vlm_table_banding import (
        normalize_kosa_cropped_employment_rows,
    )

    good = "\n".join(
        f"가상회사{i} | 199{i}.02.01 ~ 199{i}.12.31 | 업무"
        for i in range(1, 8)
    )
    assert normalize_kosa_cropped_employment_rows(good) == (
        "근무경력\n" + good
    )
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2001.01.01 ~ 2003.02.31 | 개발\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2009.01.01 ~ 2001.02.01 | 개발\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "프로젝트 | 2023.01.01 ~ 2023.12.31 | 참여기간\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2001.01.01 ~ 2003.02.01 | 업무"
    ) is None
