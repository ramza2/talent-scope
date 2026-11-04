"""Read-only live KOSA row-band pilot.

Run by concatenating this file after vlm_table_banding.py and
vlm_transcribe_table_v2.py, piping the result into the running API
container's Python interpreter. No database writes or analysis reruns.
"""

from copy import deepcopy
import os
from types import SimpleNamespace
from uuid import UUID

import pymupdf
from sqlalchemy import text

from app.ai.providers import vlm as vlm_module
from app.db.session import SessionLocal
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage

RUN = UUID(os.environ["TALENTSCOPE_KOSA_PILOT_RUN_ID"])
PAGE = 3
TARGET_BAND_INDEX = 1  # image-detected rows 6-10; no fixed pixel coordinates


def _normalize(value: str) -> str:
    return "".join((value or "").split()).lower()


def main() -> None:
    print("===== 1. ORIGINAL SOURCE (READ ONLY) =====")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        repo = AnalysisRepository(db)
        rows = repo.list_documents_by_ids(repo.list_run_document_ids(RUN))
        matches = [
            d
            for d, _group, _name in rows
            if (d.extension or "").lower() == "pdf"
            and "kosa" in d.original_filename.casefold()
        ]
        assert len(matches) == 1, f"EXPECTED_ONE_KOSA_PDF: {len(matches)}"
        document = matches[0]
        page = next(p for p in repo.list_document_pages(document.id) if p.page_no == PAGE)
        original = (
            page.extracted_text,
            page.extraction_method,
            deepcopy(page.layout_json),
        )
        snapshot = SimpleNamespace(
            id=document.id,
            extension=document.extension,
            storage_key=document.storage_key,
            preview_storage_key=document.preview_storage_key,
        )
        print("DB_READ_ONLY: on")
        print("OLD_METHOD:", original[1])
        print("OLD_CHARS:", len(original[0] or ""))
        print("OLD_LINES:", len((original[0] or "").splitlines()))

    print("===== 2. BANDS DETECTED FROM SCAN =====")
    builder = AnalysisSourceBuilder(get_object_storage())
    pdf = builder._open_render_source(snapshot)
    assert pdf is not None, "KOSA_PDF_RENDER_SOURCE_NOT_FOUND"
    try:
        assert len(pdf) >= PAGE
        pdf_page = pdf[PAGE - 1]
        dpi = 220
        bands = detect_table_row_bands(pdf_page, dpi=dpi, rows_per_band=5, max_bands=6)
        assert len(bands) == 4, f"EXPECTED_4_BANDS: {len(bands)}"
        print("BANDS:", [(b.first_row, b.last_row) for b in bands])

        band = bands[TARGET_BAND_INDEX]
        zoom = dpi / 72
        pix = pdf_page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=band.clip,
            alpha=False,
        )
        image_bytes = pix.tobytes("png")
        print("TARGET_ROW_RANGE:", (band.first_row, band.last_row))
        print("IMAGE_SIZE:", (pix.width, pix.height))
        print("IMAGE_BYTES:", len(image_bytes))
    finally:
        pdf.close()

    print("===== 3. ONE VLM REQUEST (NO DB WRITE) =====")
    provider = vlm_module.OpenAICompatibleVLMProvider()
    original_prompt = vlm_module.VLM_TRANSCRIBE_USER
    try:
        # The deployed provider may predate the optional user_prompt argument.
        # This isolated Python process does not modify the running API service.
        vlm_module.VLM_TRANSCRIBE_USER = VLM_TABLE_BAND_USER
        result = provider.transcribe_image(
            image_bytes=image_bytes,
            mime_type="image/png",
            log_context={
                "pilot": "kosa_table_banding_read_only",
                "document_id": str(snapshot.id),
                "page_no": PAGE,
                "band": f"{band.first_row}-{band.last_row}",
            },
        )
    finally:
        vlm_module.VLM_TRANSCRIBE_USER = original_prompt

    raw_lines = [line.strip() for line in result.splitlines() if line.strip()]
    cleaned = normalize_table_band_transcription(result)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    print("TRANSCRIPTION_LINES:", len(lines))
    print("RAW_COLUMN_COUNTS:", [len(line.strip("|").split("|")) for line in raw_lines])
    print("NORMALIZED_COLUMN_COUNTS:", [len(line.split("|")) for line in lines])
    print(
        "STRUCTURE_VALID:",
        is_valid_table_band_transcription(
            cleaned,
            expected_rows=band.last_row - band.first_row + 1,
            expected_columns=7,  # KOSA form header: seven visible columns
        ),
    )
    for idx, line in enumerate(lines, 1):
        print(f"{idx:02d} | {line[:350]}")

    print("===== 4. KEY 2009 ROW COMPARISON =====")
    expectations = [
        ("DW/OLAP추가개발", ("2009.03.02", "2009.04.30", "신용회복위원회", "네모소프트")),
        ("DW/OLAP성능개선", ("2009.01.02", "2009.02.27", "엔투비", "네모소프트")),
    ]
    for title, fields in expectations:
        matches = [line for line in lines if _normalize(title) in _normalize(line)]
        passed = len(matches) == 1 and all(
            _normalize(field) in _normalize(matches[0]) for field in fields
        )
        print("TARGET:", title, "PASS" if passed else "NEEDS_REVIEW")

    print("===== 5. DATABASE IMMUTABILITY =====")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AnalysisRepository(db)
        after = next(
            p for p in repo.list_document_pages(snapshot.id) if p.page_no == PAGE
        )
        assert (
            after.extracted_text,
            after.extraction_method,
            after.layout_json,
        ) == original
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
    print("DOCUMENT_PAGE_UNCHANGED: PASS")
    print("DB_STILL_READ_ONLY: on")
    print("===== V25_KOSA_ROW_BAND_READ_ONLY_PILOT_COMPLETE =====")


if __name__ == "__main__":
    main()
