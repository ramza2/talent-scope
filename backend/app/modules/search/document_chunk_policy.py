"""DOCUMENT_CHUNK SearchIndexItem / sync Job policy constants."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.document_processing.chunker import DOCUMENT_CHUNKER_VERSION
from app.modules.search.document_builder import content_hash

OPERATION_SYNC_DOCUMENT_CHUNKS = "SYNC_DOCUMENT_CHUNKS"

OBJECT_TYPE_DOCUMENT_CHUNK = "DOCUMENT_CHUNK"

# Distinct from PROFILE/PROJECT SEARCH_DOCUMENT_VERSION ("search-doc-v1").
DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION = "document-chunk-search-v1"

SOURCE_WEIGHT_DOCUMENT_CHUNK = Decimal("0.700")

DOCUMENT_CHUNKER_VERSION_META = DOCUMENT_CHUNKER_VERSION


def document_chunk_sync_fingerprint(
    *,
    document_group_id: UUID | str,
    effective_document_id: UUID | str | None,
    effective_document_updated_at: str,
    source_fingerprint: str,
    chunker_version: str,
    search_document_version: str,
    person_searchable: bool,
) -> str:
    material = "|".join(
        [
            str(document_group_id),
            str(effective_document_id or ""),
            effective_document_updated_at or "",
            source_fingerprint or "",
            chunker_version,
            search_document_version,
            "1" if person_searchable else "0",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def document_chunk_sync_idempotency_key(
    *,
    document_group_id: UUID | str,
    fingerprint: str,
) -> str:
    key = f"document-group:{document_group_id}:chunks:{fingerprint}"
    return key[:300]


def build_document_chunk_row_metadata(
    *,
    chunker_version: str,
    page_no: int,
    char_start: int,
    char_end: int,
    extraction_method: str | None,
    source_fingerprint: str,
) -> dict[str, Any]:
    return {
        "chunker_version": chunker_version,
        "page_no": page_no,
        "char_start": char_start,
        "char_end": char_end,
        "extraction_method": extraction_method,
        "source_fingerprint": source_fingerprint,
    }


def build_document_chunk_search_metadata(
    *,
    chunk_id: UUID | str,
    document_id: UUID | str,
    document_group_id: UUID | str,
    document_type_code: str | None,
    document_version_no: int | None,
    chunk_index: int,
    page_from: int,
    page_to: int,
    char_start: int,
    char_end: int,
    extraction_method: str | None,
    chunker_version: str,
    chunk_hash_value: str,
    search_text: str,
    document_date: str | None = None,
    document_title: str | None = None,
    original_filename: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "search_document_version": DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
        "content_hash": content_hash(search_text),
        "chunker_version": chunker_version,
        "chunk_hash": chunk_hash_value,
        "document_chunk_id": str(chunk_id),
        "document_id": str(document_id),
        "document_group_id": str(document_group_id),
        "document_type_code": document_type_code,
        "document_version_no": document_version_no,
        "chunk_index": chunk_index,
        "page_from": page_from,
        "page_to": page_to,
        "char_start": char_start,
        "char_end": char_end,
        "extraction_method": extraction_method,
    }
    if document_date:
        meta["document_date"] = document_date
    if document_title:
        meta["document_title"] = document_title
    if original_filename:
        meta["original_filename"] = original_filename
    return meta
