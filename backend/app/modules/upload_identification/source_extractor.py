"""Upload session identity extraction from temp files (no Document rows)."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.ai.providers.errors import AIProviderError
from app.ai.providers.vlm import VLMProvider
from app.core.config import Settings, get_settings
from app.modules.document_processing.converters.base import ConverterError, PdfConverter
from app.modules.document_processing.converters.libreoffice import LibreOfficeConverter
from app.modules.document_processing.parsers.pdf import extract_pdf_pages
from app.modules.document_processing.types import (
    CONVERT_TO_PDF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MIN_TEXT_CHARS_FOR_READY_PAGE,
)
from app.storage.base import ObjectStorage

logger = logging.getLogger(__name__)


@dataclass
class TempFileSnapshot:
    """Detached temp-file metadata for identify work outside a DB lock."""

    id: object
    original_filename: str
    document_type_code: str | None
    extension: str | None
    temp_storage_key: str


@dataclass
class ExtractedFileSource:
    temp_file_id: str
    filename: str
    document_type: str | None
    text: str = ""
    error: str | None = None
    vlm_pages_used: int = 0
    page_count: int = 0


@dataclass
class ExtractionBundle:
    sources: list[ExtractedFileSource] = field(default_factory=list)
    total_vlm_pages: int = 0

    @property
    def usable_sources(self) -> list[ExtractedFileSource]:
        return [s for s in self.sources if (s.text or "").strip()]

    def combined_document_blocks(self, max_chars: int) -> str:
        """Build LLM document blocks with a hard character cap.

        Header/footer overhead is included so ``len(result) <= max_chars``.
        """
        if max_chars <= 0:
            return ""

        parts: list[str] = []
        remaining = max_chars
        join_sep = "\n"

        for src in self.usable_sources:
            if remaining <= 0:
                break
            sep_cost = len(join_sep) if parts else 0
            if remaining <= sep_cost:
                break
            budget = remaining - sep_cost

            header = (
                "[FILE]\n"
                f"filename: {src.filename}\n"
                f"document_type: {src.document_type or ''}\n"
                "content:\n"
            )
            footer = "\n"
            overhead = len(header) + len(footer)
            if overhead > budget:
                chunk = (header + footer)[:budget]
                if chunk:
                    parts.append(chunk)
                    remaining -= sep_cost + len(chunk)
                break

            body_budget = budget - overhead
            body = (src.text or "").strip()
            if len(body) > body_budget:
                body = body[:body_budget]
            block = f"{header}{body}{footer}"
            parts.append(block)
            remaining -= sep_cost + len(block)

        result = join_sep.join(parts)
        if len(result) > max_chars:
            result = result[:max_chars]
        return result


class IdentitySourceExtractor:
    """Ephemeral temp-file → text extraction for identify (no DocumentPage)."""

    def __init__(
        self,
        storage: ObjectStorage,
        *,
        settings: Settings | None = None,
        converter: PdfConverter | None = None,
        vlm: VLMProvider | None = None,
    ) -> None:
        self.storage = storage
        self.settings = settings or get_settings()
        self.converter = converter or LibreOfficeConverter(self.settings)
        self.vlm = vlm

    def extract_session_files(
        self,
        files: list,  # UploadTempFile-like
        *,
        log_context: dict | None = None,
    ) -> ExtractionBundle:
        bundle = ExtractionBundle()
        vlm_budget = int(self.settings.identify_max_vlm_pages)
        for row in files:
            src = self._extract_one(
                row,
                vlm_budget=vlm_budget,
                log_context=log_context,
            )
            bundle.sources.append(src)
            bundle.total_vlm_pages += src.vlm_pages_used
            vlm_budget = max(0, vlm_budget - src.vlm_pages_used)
        return bundle

    def _extract_one(
        self,
        row,
        *,
        vlm_budget: int,
        log_context: dict | None,
    ) -> ExtractedFileSource:
        filename = row.original_filename
        doc_type = row.document_type_code
        file_id = str(row.id)
        ext = (row.extension or "").lower().strip()
        work_dir: Path | None = None
        try:
            work_dir = Path(tempfile.mkdtemp(prefix=f"ts-identify-{file_id}-"))
            data = self._download(row.temp_storage_key)
            if ext == "pdf":
                text, pages, vlm_used = self._from_pdf_bytes(
                    data,
                    vlm_budget=vlm_budget,
                    log_context={**(log_context or {}), "temp_file_id": file_id},
                )
                return ExtractedFileSource(
                    temp_file_id=file_id,
                    filename=filename,
                    document_type=doc_type,
                    text=text,
                    page_count=pages,
                    vlm_pages_used=vlm_used,
                )
            if ext in CONVERT_TO_PDF_EXTENSIONS:
                source_path = work_dir / f"source.{ext}"
                source_path.write_bytes(data)
                pdf_path = self.converter.convert_to_pdf(source_path, work_dir)
                pdf_bytes = pdf_path.read_bytes()
                text, pages, vlm_used = self._from_pdf_bytes(
                    pdf_bytes,
                    vlm_budget=vlm_budget,
                    log_context={**(log_context or {}), "temp_file_id": file_id},
                )
                return ExtractedFileSource(
                    temp_file_id=file_id,
                    filename=filename,
                    document_type=doc_type,
                    text=text,
                    page_count=pages,
                    vlm_pages_used=vlm_used,
                )
            if ext in IMAGE_EXTENSIONS:
                text = ""
                vlm_used = 0
                if self.vlm is not None and vlm_budget > 0:
                    mime = "image/png" if ext == "png" else "image/jpeg"
                    text = self.vlm.transcribe_image(
                        image_bytes=data,
                        mime_type=mime,
                        log_context={**(log_context or {}), "temp_file_id": file_id},
                    )
                    vlm_used = 1
                return ExtractedFileSource(
                    temp_file_id=file_id,
                    filename=filename,
                    document_type=doc_type,
                    text=text or "",
                    page_count=1,
                    vlm_pages_used=vlm_used,
                )
            return ExtractedFileSource(
                temp_file_id=file_id,
                filename=filename,
                document_type=doc_type,
                error=f"unsupported extension .{ext or '?'}",
            )
        except (ConverterError, AIProviderError) as exc:
            logger.info(
                "identify extraction file failure temp_file_id=%s err=%s",
                file_id,
                type(exc).__name__,
            )
            return ExtractedFileSource(
                temp_file_id=file_id,
                filename=filename,
                document_type=doc_type,
                error=str(exc)[:500],
            )
        except Exception as exc:
            logger.exception("identify extraction unexpected temp_file_id=%s", file_id)
            return ExtractedFileSource(
                temp_file_id=file_id,
                filename=filename,
                document_type=doc_type,
                error=f"{type(exc).__name__}",
            )
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _download(self, key: str) -> bytes:
        obj = self.storage.get(key)
        try:
            data = b"".join(obj.iter_chunks())
        finally:
            obj.close()
        if not data:
            raise AIProviderError("empty temp object")
        return data

    def _from_pdf_bytes(
        self,
        pdf_bytes: bytes,
        *,
        vlm_budget: int,
        log_context: dict | None,
    ) -> tuple[str, int, int]:
        max_pages = int(self.settings.identify_max_pages)
        result = extract_pdf_pages(pdf_bytes)
        parts: list[str] = []
        vlm_used = 0
        pages = result.pages[:max_pages]
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in pages:
                text = (page.extracted_text or "").strip()
                needs_vlm = bool(
                    page.layout_json and page.layout_json.get("needs_vlm")
                ) or len(text) < MIN_TEXT_CHARS_FOR_READY_PAGE
                if needs_vlm and self.vlm is not None and vlm_used < vlm_budget:
                    image_bytes = self._render_page_png(doc, page.page_no - 1)
                    transcribed = self.vlm.transcribe_image(
                        image_bytes=image_bytes,
                        mime_type="image/png",
                        log_context={
                            **(log_context or {}),
                            "page_no": page.page_no,
                        },
                    )
                    vlm_used += 1
                    if transcribed.strip():
                        text = f"{text}\n{transcribed.strip()}".strip()
                if text:
                    parts.append(f"[PAGE {page.page_no}]\n{text}")
        return "\n\n".join(parts), result.page_count, vlm_used

    def _render_page_png(self, doc: pymupdf.Document, page_index: int) -> bytes:
        # Modest DPI to keep VLM payload bounded.
        dpi = int(self.settings.identify_pdf_render_dpi)
        zoom = max(dpi, 72) / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return pix.tobytes("png")
