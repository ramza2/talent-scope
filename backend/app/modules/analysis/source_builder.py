"""Build LLM document blocks from READY DocumentPage text (+ optional VLM)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pymupdf

from app.ai.providers.errors import AIProviderError
from app.ai.providers.vlm import VLMProvider
from app.core.config import Settings, get_settings
from app.modules.document_processing.types import (
    IMAGE_EXTENSIONS,
    MIN_TEXT_CHARS_FOR_READY_PAGE,
)
from app.storage.base import ObjectStorage

logger = logging.getLogger(__name__)


@dataclass
class PageSnapshot:
    page_no: int
    extracted_text: str | None
    layout_json: dict[str, Any] | None
    extraction_method: str | None = None


@dataclass
class DocumentSnapshot:
    """Detached document metadata — no ORM outside short TX."""

    id: UUID
    original_filename: str
    extension: str | None
    mime_type: str | None
    storage_key: str
    preview_storage_key: str | None
    document_type_code: str | None
    document_type_name: str | None
    version_no: int
    person_id: UUID
    pages: list[PageSnapshot] = field(default_factory=list)


@dataclass
class DocumentSourceBlock:
    document_id: str
    filename: str
    document_type: str | None
    text: str = ""
    error: str | None = None
    vlm_pages_used: int = 0
    page_count: int = 0


@dataclass
class PromptSource:
    """Text actually sent to the LLM, plus page scope for source_ref validation."""

    text: str
    allowed_documents: dict[str, set[int]] = field(default_factory=dict)
    page_texts: dict[tuple[str, int], str] = field(default_factory=dict)


def _parse_page_segments(body: str) -> list[tuple[int, str]]:
    """Split DocumentSourceBlock body into ``(page_no, page_text)`` segments."""
    segments: list[tuple[int, str]] = []
    current_page: int | None = None
    buf: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("[PAGE ") and line.endswith("]"):
            if current_page is not None:
                segments.append((current_page, "\n".join(buf).strip()))
            buf = []
            try:
                current_page = int(line[6:-1].strip())
            except ValueError:
                current_page = None
            continue
        if current_page is not None:
            buf.append(line)
    if current_page is not None:
        segments.append((current_page, "\n".join(buf).strip()))
    return segments


@dataclass
class AnalysisSourceBundle:
    blocks: list[DocumentSourceBlock] = field(default_factory=list)
    total_vlm_pages: int = 0

    @property
    def usable_blocks(self) -> list[DocumentSourceBlock]:
        return [b for b in self.blocks if (b.text or "").strip()]

    def combined_document_blocks(self, max_chars: int) -> str:
        """Build ``[DOCUMENT]`` / ``[PAGE n]`` blocks with a hard char cap."""
        return self.build_prompt_source(max_chars).text

    def build_prompt_source(self, max_chars: int) -> PromptSource:
        """Build LLM document text and the exact page scope included in it."""
        if max_chars <= 0:
            return PromptSource(text="")

        parts: list[str] = []
        remaining = max_chars
        join_sep = "\n\n"
        allowed: dict[str, set[int]] = {}
        page_texts: dict[tuple[str, int], str] = {}

        for src in self.usable_blocks:
            if remaining <= 0:
                break
            sep_cost = len(join_sep) if parts else 0
            if remaining <= sep_cost:
                break
            budget = remaining - sep_cost

            header = (
                "[DOCUMENT]\n"
                f"document_id: {src.document_id}\n"
                f"filename: {src.filename}\n"
                f"document_type: {src.document_type or ''}\n"
                "content:\n"
            )
            footer = "\n"
            overhead = len(header) + len(footer)
            if overhead >= budget:
                chunk = (header + footer)[:budget]
                if chunk:
                    parts.append(chunk)
                    remaining -= sep_cost + len(chunk)
                break

            body_budget = budget - overhead
            page_parts: list[str] = []
            used_body = 0
            segments = _parse_page_segments((src.text or "").strip())
            if not segments:
                # No page markers — include truncated whole body without page refs.
                body = (src.text or "").strip()
                if len(body) > body_budget:
                    body = body[:body_budget]
                block = f"{header}{body}{footer}"
                parts.append(block)
                remaining -= sep_cost + len(block)
                continue

            for page_no, page_text in segments:
                page_header = f"[PAGE {page_no}]\n"
                sep = "\n\n" if page_parts else ""
                available = body_budget - used_body - len(sep) - len(page_header)
                if available <= 0:
                    break
                included = page_text
                truncated = False
                if len(included) > available:
                    included = included[:available]
                    truncated = True
                if not included and not page_text:
                    # Empty page still marks presence if header fits.
                    pass
                chunk = f"{sep}{page_header}{included}"
                page_parts.append(chunk)
                used_body += len(chunk)
                allowed.setdefault(src.document_id, set()).add(page_no)
                page_texts[(src.document_id, page_no)] = included
                if truncated:
                    break

            body = "".join(page_parts)
            block = f"{header}{body}{footer}"
            parts.append(block)
            remaining -= sep_cost + len(block)

        result = join_sep.join(parts)
        if len(result) > max_chars:
            result = result[:max_chars]
        return PromptSource(
            text=result,
            allowed_documents=allowed,
            page_texts=page_texts,
        )


class AnalysisSourceBuilder:
    """DocumentPage text (+ VLM for needs_vlm pages). Does not create Evidence rows."""

    def __init__(
        self,
        storage: ObjectStorage,
        *,
        settings: Settings | None = None,
        vlm: VLMProvider | None = None,
    ) -> None:
        self.storage = storage
        self.settings = settings or get_settings()
        self.vlm = vlm

    def build(
        self,
        documents: list[DocumentSnapshot],
        *,
        log_context: dict | None = None,
    ) -> AnalysisSourceBundle:
        bundle = AnalysisSourceBundle()
        vlm_budget = int(self.settings.analysis_max_vlm_pages)
        for doc in documents:
            block = self._build_one(
                doc,
                vlm_budget=vlm_budget,
                log_context=log_context,
            )
            bundle.blocks.append(block)
            bundle.total_vlm_pages += block.vlm_pages_used
            vlm_budget = max(0, vlm_budget - block.vlm_pages_used)
        return bundle

    def _build_one(
        self,
        doc: DocumentSnapshot,
        *,
        vlm_budget: int,
        log_context: dict | None,
    ) -> DocumentSourceBlock:
        doc_id = str(doc.id)
        max_pages = int(self.settings.analysis_max_pages_per_document)
        pages = list(doc.pages)[:max_pages]
        try:
            parts: list[str] = []
            vlm_used = 0
            pdf_doc: pymupdf.Document | None = None
            try:
                for page in pages:
                    text = (page.extracted_text or "").strip()
                    needs_vlm = bool(
                        page.layout_json and page.layout_json.get("needs_vlm")
                    ) or len(text) < MIN_TEXT_CHARS_FOR_READY_PAGE

                    if needs_vlm and self.vlm is not None and vlm_used < vlm_budget:
                        if pdf_doc is None:
                            pdf_doc = self._open_render_source(doc)
                        if pdf_doc is not None:
                            image_bytes = self._render_page_png(
                                pdf_doc, page.page_no - 1
                            )
                            transcribed = self.vlm.transcribe_image(
                                image_bytes=image_bytes,
                                mime_type="image/png",
                                log_context={
                                    **(log_context or {}),
                                    "document_id": doc_id,
                                    "page_no": page.page_no,
                                },
                            )
                            vlm_used += 1
                            if transcribed and transcribed.strip():
                                text = f"{text}\n{transcribed.strip()}".strip()
                        elif self._is_image(doc) and vlm_used < vlm_budget:
                            image_bytes = self._download(doc.storage_key)
                            mime = (
                                "image/png"
                                if (doc.extension or "").lower() == "png"
                                else "image/jpeg"
                            )
                            transcribed = self.vlm.transcribe_image(
                                image_bytes=image_bytes,
                                mime_type=mime,
                                log_context={
                                    **(log_context or {}),
                                    "document_id": doc_id,
                                    "page_no": page.page_no,
                                },
                            )
                            vlm_used += 1
                            if transcribed and transcribed.strip():
                                text = f"{text}\n{transcribed.strip()}".strip()

                    if text:
                        parts.append(f"[PAGE {page.page_no}]\n{text}")
            finally:
                if pdf_doc is not None:
                    pdf_doc.close()

            return DocumentSourceBlock(
                document_id=doc_id,
                filename=doc.original_filename,
                document_type=doc.document_type_code,
                text="\n\n".join(parts),
                page_count=len(pages),
                vlm_pages_used=vlm_used,
            )
        except (AIProviderError, Exception) as exc:
            logger.info(
                "analysis source build failure document_id=%s err=%s",
                doc_id,
                type(exc).__name__,
            )
            return DocumentSourceBlock(
                document_id=doc_id,
                filename=doc.original_filename,
                document_type=doc.document_type_code,
                error=f"{type(exc).__name__}: {str(exc)[:400]}",
                page_count=len(pages),
            )

    def _is_image(self, doc: DocumentSnapshot) -> bool:
        ext = (doc.extension or "").lower().strip()
        return ext in IMAGE_EXTENSIONS

    def _open_render_source(self, doc: DocumentSnapshot) -> pymupdf.Document | None:
        """Prefer preview PDF, then original PDF; images handled separately."""
        ext = (doc.extension or "").lower().strip()
        keys: list[str] = []
        if doc.preview_storage_key:
            keys.append(doc.preview_storage_key)
        if ext == "pdf":
            keys.append(doc.storage_key)
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            try:
                data = self._download(key)
                return pymupdf.open(stream=data, filetype="pdf")
            except Exception:
                logger.info(
                    "analysis render open failed document_id=%s key=%s",
                    doc.id,
                    key,
                )
                continue
        return None

    def _download(self, key: str) -> bytes:
        obj = self.storage.get(key)
        try:
            data = b"".join(obj.iter_chunks())
        finally:
            obj.close()
        if not data:
            raise AIProviderError("empty object")
        return data

    def _render_page_png(self, doc: pymupdf.Document, page_index: int) -> bytes:
        dpi = int(self.settings.analysis_pdf_render_dpi)
        zoom = max(dpi, 72) / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return pix.tobytes("png")
