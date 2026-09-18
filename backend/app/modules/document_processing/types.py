"""Document processing types and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Formats that need LibreOffice → PDF before text extraction.
CONVERT_TO_PDF_EXTENSIONS: frozenset[str] = frozenset(
    {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}
)

# Hancom formats have a native text-extraction path.  LibreOffice may still be
# attempted by document processing when a PDF preview can be produced.
HANCOM_NATIVE_EXTENSIONS: frozenset[str] = frozenset({"hwp", "hwpx"})

# PPTX has a safe OOXML text fallback when LibreOffice cannot render it.
PPTX_NATIVE_EXTENSIONS: frozenset[str] = frozenset({"pptx"})

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
