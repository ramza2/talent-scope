"""Deterministic page-local DocumentChunk specs from DocumentPage.extracted_text."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

DOCUMENT_CHUNKER_VERSION = "chunk-v1"

# Character-based MVP policy (no tokenizer dependency).
DOCUMENT_CHUNK_MAX_CHARS = 1600
DOCUMENT_CHUNK_OVERLAP_CHARS = 200


@dataclass(frozen=True)
class ChunkSpec:
    """One page-local chunk candidate before persistence."""

    chunk_index: int
    page_no: int
    char_start: int  # inclusive
    char_end: int  # exclusive (Python slice semantics)
    chunk_text: str
    chunk_hash: str
    extraction_method: str | None
    source_fingerprint: str

    @property
    def page_from(self) -> int:
        return self.page_no

    @property
    def page_to(self) -> int:
        return self.page_no


def chunk_hash(chunk_text: str) -> str:
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()


def pages_source_fingerprint(
    pages: Sequence[Any],
    *,
    chunker_version: str = DOCUMENT_CHUNKER_VERSION,
) -> str:
    """Deterministic fingerprint of ordered page extraction state + chunker version.

    Does not include raw page text in logs — only hashed material.
    """
    parts: list[str] = [chunker_version]
    for page in pages:
        page_no = int(getattr(page, "page_no"))
        method = getattr(page, "extraction_method", None) or ""
        text = getattr(page, "extracted_text", None) or ""
        text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        parts.append(f"{page_no}|{method}|{text_digest}")
    material = "\n".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _find_cut(text: str, start: int, hard_end: int) -> int:
    """Prefer paragraph/newline, then whitespace, else hard cut at hard_end."""
    if hard_end >= len(text):
        return len(text)
    window = text[start:hard_end]
    # Prefer last paragraph / newline boundary in the second half of the window.
    search_from = max(0, len(window) // 2)
    for sep in ("\n\n", "\n"):
        idx = window.rfind(sep, search_from)
        if idx >= 0:
            cut = start + idx + len(sep)
            if cut > start:
                return cut
    # Whitespace boundary
    idx = -1
    for i in range(len(window) - 1, search_from - 1, -1):
        if window[i].isspace():
            idx = i
            break
    if idx >= 0:
        cut = start + idx + 1
        if cut > start:
            return cut
    return hard_end


def _trim_slice(text: str, start: int, end: int) -> tuple[int, int, str]:
    """Drop leading/trailing whitespace from [start:end] while adjusting offsets."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end, text[start:end]


def chunk_page_text(
    text: str,
    *,
    page_no: int,
    extraction_method: str | None,
    source_fingerprint: str,
    start_index: int,
    max_chars: int = DOCUMENT_CHUNK_MAX_CHARS,
    overlap_chars: int = DOCUMENT_CHUNK_OVERLAP_CHARS,
) -> list[ChunkSpec]:
    """Build page-local chunks. ``page_from == page_to == page_no``."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be non-negative")
    if overlap_chars >= max_chars:
        # Prevent zero/negative progress.
        overlap_chars = max_chars - 1

    if not (text or "").strip():
        return []

    specs: list[ChunkSpec] = []
    n = len(text)
    pos = 0
    index = start_index
    # Skip leading whitespace once so first chunk starts on content.
    while pos < n and text[pos].isspace():
        pos += 1

    while pos < n:
        hard_end = min(pos + max_chars, n)
        cut = _find_cut(text, pos, hard_end)
        if cut <= pos:
            # Guaranteed forward progress.
            cut = min(pos + max_chars, n)
            if cut <= pos:
                break
        slice_start, slice_end, chunk_text = _trim_slice(text, pos, cut)
        if chunk_text:
            specs.append(
                ChunkSpec(
                    chunk_index=index,
                    page_no=page_no,
                    char_start=slice_start,
                    char_end=slice_end,
                    chunk_text=chunk_text,
                    chunk_hash=chunk_hash(chunk_text),
                    extraction_method=extraction_method,
                    source_fingerprint=source_fingerprint,
                )
            )
            index += 1
            # Verify exact substring.
            assert text[slice_start:slice_end] == chunk_text

        if cut >= n:
            break

        next_pos = cut - overlap_chars
        if next_pos <= pos:
            next_pos = cut
        # Skip whitespace-only progress stalls.
        while next_pos < n and text[next_pos].isspace() and next_pos < cut:
            next_pos += 1
        if next_pos <= pos:
            next_pos = cut
        pos = next_pos

    return specs


def build_chunk_specs_for_pages(
    pages: Sequence[Any],
    *,
    max_chars: int = DOCUMENT_CHUNK_MAX_CHARS,
    overlap_chars: int = DOCUMENT_CHUNK_OVERLAP_CHARS,
    chunker_version: str = DOCUMENT_CHUNKER_VERSION,
) -> tuple[list[ChunkSpec], str]:
    """Chunk each non-blank page independently; global chunk_index 0..N-1."""
    ordered = sorted(pages, key=lambda p: int(getattr(p, "page_no")))
    fingerprint = pages_source_fingerprint(ordered, chunker_version=chunker_version)
    specs: list[ChunkSpec] = []
    next_index = 0
    for page in ordered:
        page_no = int(getattr(page, "page_no"))
        text = getattr(page, "extracted_text", None) or ""
        method = getattr(page, "extraction_method", None)
        page_specs = chunk_page_text(
            text,
            page_no=page_no,
            extraction_method=method,
            source_fingerprint=fingerprint,
            start_index=next_index,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
        specs.extend(page_specs)
        next_index = start_index_after(specs)
    return specs, fingerprint


def start_index_after(specs: Sequence[ChunkSpec]) -> int:
    if not specs:
        return 0
    return int(specs[-1].chunk_index) + 1
