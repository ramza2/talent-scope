"""Document processing types and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Office formats that use LibreOffice -> PDF before text extraction.
OFFICE_CONVERT_TO_PDF_EXTENSIONS: frozenset[str] = frozenset(
    {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}
)

# HWP 5.x / HWPX use native text extraction for analysis and may still use
# LibreOffice opportunistically when a PDF preview can be produced.
KOREAN_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({"hwp", "hwpx"})

# Backward-compatible aggregate for callers that only need the supported set.
CONVERT_TO_PDF_EXTENSIONS: frozenset[str] = (
    OFFICE_CONVERT_TO_PDF_EXTENSIONS | KOREAN_DOCUMENT_EXTENSIONS
)

IMAGE_EXTENSIONS: frozenset[str] = frozenset({"jpg", "jpeg", "png"})

# Pages with fewer stripped chars are marked needs_vlm (no failure).
MIN_TEXT_CHARS_FOR_READY_PAGE = 20


@dataclass
class ExtractedPage:
    page_no: int
    extracted_text: str | None
    extraction_method: str | None
    layout_json: dict[str, Any] | None = None


@dataclass
class ExtractionResult:
    pages: list[ExtractedPage] = field(default_factory=list)
    page_count: int = 0
    # When set, this PDF should be stored as preview_storage_key.
    # None means use original as preview source (native PDF / images).
    preview_pdf_bytes: bytes | None = None
    uses_original_as_preview: bool = False
