"""PyMuPDF-based PDF page text extraction."""

from __future__ import annotations

import logging

import pymupdf

from app.modules.document_processing.types import (
    MIN_TEXT_CHARS_FOR_READY_PAGE,
    ExtractedPage,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


def extract_pdf_pages(pdf_bytes: bytes) -> ExtractionResult:
    """Extract per-page text from a PDF. Does not call VLM/OCR."""
    pages: list[ExtractedPage] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            stripped = text.strip()
            layout = None
            if len(stripped) < MIN_TEXT_CHARS_FOR_READY_PAGE:
                layout = {"needs_vlm": True, "text_chars": len(stripped)}
            pages.append(
                ExtractedPage(
                    page_no=index + 1,
                    extracted_text=text if text else None,
                    extraction_method="TEXT_PARSER",
                    layout_json=layout,
                )
            )
        page_count = int(doc.page_count)
    return ExtractionResult(
        pages=pages,
        page_count=page_count,
        preview_pdf_bytes=None,
        uses_original_as_preview=True,
    )


def build_minimal_pdf_with_text(text: str = "TalentScope test page") -> bytes:
    """Utility for tests: single-page PDF with drawable text."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data
