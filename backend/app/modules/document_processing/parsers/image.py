"""Image document page stub (VLM deferred)."""

from __future__ import annotations

from app.modules.document_processing.types import ExtractedPage, ExtractionResult


def extract_image_page() -> ExtractionResult:
    """Images are previewable as originals; text awaits VLM in a later stage."""
    return ExtractionResult(
        pages=[
            ExtractedPage(
                page_no=1,
                extracted_text=None,
                extraction_method=None,
                layout_json={"needs_vlm": True, "source": "image"},
            )
        ],
        page_count=1,
        preview_pdf_bytes=None,
        uses_original_as_preview=True,
    )
