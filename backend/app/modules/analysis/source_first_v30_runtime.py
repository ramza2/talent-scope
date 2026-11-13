"""Opt-in native-PDF Source-First adapter for a newly analyzed candidate.

No database writes or decisions. Missing/unverifiable rows raise ValueError;
the caller retains the complete validated v25 Candidate and Diff.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.project_row_grouping import native_table_groups
from app.modules.analysis.project_source_first_native_direct_v30 import (
    NativeDirectPreview, assemble_native_direct,
)
from app.modules.analysis.source_builder import AnalysisSourceBuilder, DocumentSnapshot

_MAX_DOCUMENTS = 12
_MAX_PAGES_PER_DOCUMENT = 80
_MAX_NATIVE_ROWS = 500


def assemble_live_source_first(
    *,
    source_builder: AnalysisSourceBuilder,
    documents: Sequence[DocumentSnapshot],
    original_candidate: ProfileCandidateDocument,
    confirmed_snapshot: dict[str, Any],
    page_texts: dict[tuple[str, int], str],
) -> NativeDirectPreview:
    """Source-First only when every submitted document has native table rows.

    A mixed/non-PDF document batch must not silently replace v25 projects
    with a subset from the supported native-PDF files.
    """
    if not documents or len(documents) > _MAX_DOCUMENTS:
        raise ValueError("V30_SOURCE_DOCUMENT_COUNT_OUT_OF_SCOPE")

    native_rows: list[dict[str, Any]] = []
    for doc in documents:
        if not doc.pages or len(doc.pages) > _MAX_PAGES_PER_DOCUMENT:
            raise ValueError("V30_SOURCE_PAGE_COUNT_OUT_OF_SCOPE")
        pdf = source_builder._open_render_source(doc)
        if pdf is None:
            raise ValueError("V30_SOURCE_PDF_UNAVAILABLE")
        document_rows: list[dict[str, Any]] = []
        try:
            for page in doc.pages:
                if page.page_no < 1 or page.page_no > len(pdf):
                    raise ValueError("V30_SOURCE_PAGE_MISMATCH")
                document_rows.extend(native_table_groups(
                    pdf.load_page(page.page_no - 1), str(doc.id), page.page_no,
                ))
                if len(native_rows) + len(document_rows) > _MAX_NATIVE_ROWS:
                    raise ValueError("V30_SOURCE_ROW_LIMIT_EXCEEDED")
        finally:
            pdf.close()
        if not document_rows:
            raise ValueError("V30_SOURCE_DOCUMENT_WITHOUT_NATIVE_ROWS")
        native_rows.extend(document_rows)

    return assemble_native_direct(
        native_rows=native_rows,
        original_candidate_json=original_candidate.to_storage_dict(),
        latest_confirmed_snapshot=confirmed_snapshot,
        page_texts=page_texts,
    )
