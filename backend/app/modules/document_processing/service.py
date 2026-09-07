"""Document processing orchestration: convert → extract → DocumentPage."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, StorageError
from app.db.models.document import Document
from app.modules.document_processing.converters.base import ConverterError, PdfConverter
from app.modules.document_processing.converters.libreoffice import LibreOfficeConverter
from app.modules.document_processing.parsers.image import extract_image_page
from app.modules.document_processing.parsers.pdf import extract_pdf_pages
from app.modules.document_processing.repository import DocumentProcessingRepository
from app.modules.document_processing.types import (
    CONVERT_TO_PDF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    ExtractionResult,
)
from app.storage.base import ObjectStorage
from app.storage.s3 import get_object_storage

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    def __init__(
        self,
        db: Session,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
        converter: PdfConverter | None = None,
    ) -> None:
        self.db = db
        self.repo = DocumentProcessingRepository(db)
        self.settings = settings or get_settings()
        self.storage = storage or get_object_storage()
        self.converter = converter or LibreOfficeConverter(self.settings)

    def preview_key(
        self,
        person_id: UUID,
        group_id: UUID,
        document_id: UUID,
        *,
        preview_id: UUID | None = None,
    ) -> str:
        """Opaque unique preview object key (never overwrite an existing preview)."""
        pid = preview_id or uuid4()
        return f"documents/{person_id}/{group_id}/{document_id}/previews/{pid}.pdf"

    def process_document(self, document_id: UUID) -> str:
        """Process one document to READY or FAILED.

        Returns final ``processing_status``. Concurrent callers lose the atomic
        claim and return the current status without running conversion.
        """
        # Atomic claim closes the race between SKIP LOCKED select and status commit.
        if not self.repo.try_claim_processing(document_id):
            existing = self.db.execute(
                select(Document).where(Document.id == document_id)
            ).scalar_one_or_none()
            if existing is None or existing.deleted_at is not None:
                raise NotFoundError("문서를 찾을 수 없습니다.")
            logger.info(
                "process_document skipped (already processing) document_id=%s status=%s",
                document_id,
                existing.processing_status,
            )
            return existing.processing_status

        self.db.commit()

        # Re-lock after claim commit for the remainder of the unit of work.
        document = self.repo.get_document_for_update(document_id)
        if document is None:
            raise NotFoundError("문서를 찾을 수 없습니다.")

        group = self.repo.get_group(document.document_group_id)
        if group is None:
            raise NotFoundError("문서 그룹을 찾을 수 없습니다.")

        previous_preview_key = document.preview_storage_key
        new_preview_key: str | None = None
        uploaded_new_preview = False

        work_dir: Path | None = None
        try:
            work_dir = Path(tempfile.mkdtemp(prefix=f"ts-doc-{document_id}-"))
            original_bytes = self._download_original(document)
            ext = (document.extension or "").lower().strip()

            result = self._build_extraction(
                document=document,
                extension=ext,
                original_bytes=original_bytes,
                work_dir=work_dir,
            )

            if result.preview_pdf_bytes is not None:
                # Always use a fresh unique key so a failed reprocess never
                # overwrites / compensates-away a previously good preview.
                new_preview_key = self.preview_key(
                    group.person_id, group.id, document.id
                )
                self.storage.put_bytes(
                    new_preview_key,
                    result.preview_pdf_bytes,
                    content_type="application/pdf",
                )
                uploaded_new_preview = True

            # Prefer new preview key; native PDF/image keep previous None.
            final_preview_key = (
                new_preview_key
                if uploaded_new_preview
                else (
                    None
                    if result.uses_original_as_preview
                    else previous_preview_key
                )
            )

            self.repo.replace_pages(document.id, result.pages)
            self.repo.mark_ready(
                document,
                preview_storage_key=final_preview_key,
                preview_page_count=result.page_count,
            )
            self.db.commit()

            # After READY is committed, remove obsolete previous preview if replaced.
            if (
                uploaded_new_preview
                and previous_preview_key
                and previous_preview_key != new_preview_key
            ):
                try:
                    self.storage.delete(previous_preview_key)
                except Exception:
                    logger.warning(
                        "orphan old preview after reprocess key=%s",
                        previous_preview_key,
                        exc_info=True,
                    )
            return "READY"
        except Exception as exc:
            logger.exception("document processing failed document_id=%s", document_id)
            self.db.rollback()
            # Compensate only the newly uploaded unique preview; never touch
            # previous_preview_key / object (may still be the live READY preview).
            if uploaded_new_preview and new_preview_key:
                try:
                    self.storage.delete(new_preview_key)
                except Exception:
                    logger.warning(
                        "compensate delete preview failed key=%s",
                        new_preview_key,
                        exc_info=True,
                    )
            # Preserve original object and existing preview_storage_key; mark FAILED.
            document = self.repo.get_document_for_update(document_id)
            if document is not None:
                self.repo.mark_failed(document, self._safe_error(exc))
                self.db.commit()
                return "FAILED"
            raise
        finally:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)

    def _download_original(self, document: Document) -> bytes:
        # TODO(perf): stream ObjectStorage directly to work_dir source file to
        # avoid holding up to ~50MB twice in Python memory (chunks list + join).
        # Prefer path-based PyMuPDF open / LibreOffice source path after that.
        obj = self.storage.get(document.storage_key)
        try:
            chunks = list(obj.iter_chunks())
        finally:
            obj.close()
        data = b"".join(chunks)
        if not data:
            raise StorageError("원본 객체가 비어 있습니다.")
        return data

    def _build_extraction(
        self,
        *,
        document: Document,
        extension: str,
        original_bytes: bytes,
        work_dir: Path,
    ) -> ExtractionResult:
        if extension == "pdf":
            result = extract_pdf_pages(original_bytes)
            result.uses_original_as_preview = True
            result.preview_pdf_bytes = None
            return result

        if extension in IMAGE_EXTENSIONS:
            return extract_image_page()

        if extension in CONVERT_TO_PDF_EXTENSIONS:
            return self._convert_and_extract(
                extension=extension,
                original_bytes=original_bytes,
                original_filename=document.original_filename,
                work_dir=work_dir,
            )

        raise ConverterError(f"지원하지 않는 처리 확장자입니다: .{extension or '?'}")

    def _convert_and_extract(
        self,
        *,
        extension: str,
        original_bytes: bytes,
        original_filename: str,
        work_dir: Path,
    ) -> ExtractionResult:
        # Keep a safe local name; never pass user path to a shell.
        safe_name = f"source.{extension}"
        source_path = work_dir / safe_name
        source_path.write_bytes(original_bytes)
        try:
            pdf_path = self.converter.convert_to_pdf(source_path, work_dir)
        except ConverterError:
            raise
        except Exception as exc:
            raise ConverterError(f"변환기 오류: {exc}") from exc

        pdf_bytes = pdf_path.read_bytes()
        if not pdf_bytes.startswith(b"%PDF"):
            raise ConverterError("변환 결과가 유효한 PDF가 아닙니다.")

        result = extract_pdf_pages(pdf_bytes)
        result.preview_pdf_bytes = pdf_bytes
        result.uses_original_as_preview = False
        return result

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        msg = str(exc).strip() or exc.__class__.__name__
        # Never dump full stack traces into the DB column for UI leakage risk.
        return msg[:2000]
