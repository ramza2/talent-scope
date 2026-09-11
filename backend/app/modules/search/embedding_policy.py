"""Embedding pipeline policy — model/version, input prep, idempotency helpers."""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import get_settings
from app.db.models.search import EMBEDDING_DIMENSIONS
from app.modules.search.document_builder import content_hash

# Bump when preprocessing / storage policy changes (not when model name changes).
EMBEDDING_PIPELINE_VERSION = "embed-v1"

OPERATION_EMBED_SEARCH_INDEX_ITEM = "EMBED_SEARCH_INDEX_ITEM"


class EmbeddingConfigurationError(Exception):
    """Settings disagree with the fixed DB VECTOR dimension."""


def assert_embedding_dimensions_configured() -> int:
    """Return configured dimensions or raise if they disagree with DB SoT."""
    settings = get_settings()
    configured = int(settings.embedding_dimensions)
    if configured != EMBEDDING_DIMENSIONS:
        raise EmbeddingConfigurationError(
            f"embedding_dimensions={configured} does not match "
            f"DB VECTOR({EMBEDDING_DIMENSIONS})"
        )
    return EMBEDDING_DIMENSIONS


def effective_embedding_version(*, max_input_chars: int | None = None) -> str:
    """Deterministic embedding_version including pipeline + input-cap policy."""
    settings = get_settings()
    cap = int(
        max_input_chars
        if max_input_chars is not None
        else settings.embedding_max_input_chars
    )
    version = f"{EMBEDDING_PIPELINE_VERSION}:c{cap}"
    return version[:100]


def current_embedding_model() -> str:
    return (get_settings().embedding_model or "").strip()


def item_content_hash(item: Any) -> str:
    """Resolve content_hash from metadata_json or recompute from search_text."""
    meta = item.metadata_json if isinstance(getattr(item, "metadata_json", None), dict) else {}
    raw = meta.get("content_hash")
    if isinstance(raw, str) and len(raw) == 64 and all(c in "0123456789abcdef" for c in raw.lower()):
        return raw.lower()
    return content_hash(item.search_text or "")


def item_search_document_version(item: Any) -> str:
    """Resolve search_document_version from metadata_json (empty string if absent)."""
    meta = item.metadata_json if isinstance(getattr(item, "metadata_json", None), dict) else {}
    raw = meta.get("search_document_version")
    if isinstance(raw, str):
        return raw
    return ""


def prepare_embedding_input(search_text: str, *, max_chars: int | None = None) -> str:
    """Strip and deterministically cap text for the embedding API (DB text unchanged)."""
    settings = get_settings()
    cap = int(max_chars if max_chars is not None else settings.embedding_max_input_chars)
    if cap <= 0:
        raise ValueError("embedding_max_input_chars must be positive")
    text = (search_text or "").strip()
    if not text:
        raise ValueError("embedding input is blank")
    if len(text) > cap:
        return text[:cap]
    return text


def embedding_fingerprint(
    *,
    search_index_item_id: str,
    content_hash_value: str,
    search_document_version: str,
    embedding_model: str,
    embedding_version: str,
) -> str:
    material = "|".join(
        [
            str(search_index_item_id),
            content_hash_value,
            search_document_version,
            embedding_model,
            embedding_version,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def embedding_idempotency_key(
    *,
    search_index_item_id: str,
    content_hash_value: str,
    search_document_version: str,
    embedding_model: str,
    embedding_version: str,
) -> str:
    fp = embedding_fingerprint(
        search_index_item_id=search_index_item_id,
        content_hash_value=content_hash_value,
        search_document_version=search_document_version,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
    )
    key = f"search-index:{search_index_item_id}:embed:{fp}"
    return key[:300]


def item_needs_embedding(item: Any, *, model: str, version: str) -> bool:
    if not bool(getattr(item, "is_active", False)):
        return False
    if getattr(item, "object_type", None) not in {"PROFILE", "PROJECT", "DOCUMENT_CHUNK"}:
        return False
    if not (getattr(item, "search_text", None) or "").strip():
        return False
    if getattr(item, "embedding", None) is None:
        return True
    if (getattr(item, "embedding_model", None) or "") != model:
        return True
    if (getattr(item, "embedding_version", None) or "") != version:
        return True
    return False
