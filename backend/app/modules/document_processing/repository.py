"""Persistence helpers for document processing."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models.document import Document, DocumentGroup, DocumentPage
from app.modules.document_processing.types import ExtractedPage


class DocumentProcessingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_document(self, document_id: UUID) -> Document | None:
        return self.db.execute(
            select(Document).where(
                Document.id == document_id, Document.deleted_at.is_(None)
            )
        ).scalar_one_or_none()

    def get_document_for_update(
        self, document_id: UUID, *, skip_locked: bool = False
    ) -> Document | None:
        stmt = select(Document).where(
            Document.id == document_id, Document.deleted_at.is_(None)
        )
        if skip_locked:
            stmt = stmt.with_for_update(skip_locked=True)
        else:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_group(self, group_id: UUID) -> DocumentGroup | None:
        return self.db.execute(
            select(DocumentGroup).where(DocumentGroup.id == group_id)
        ).scalar_one_or_none()

    def try_claim_processing(self, document_id: UUID) -> bool:
        """Atomically claim a document for processing.

        Succeeds only when the row exists, is not soft-deleted, and is not
        already ``PROCESSING``. READY / FAILED / UPLOADED can be reclaimed for
        explicit re-runs. Returns True iff this session won the claim.
        """
        result = self.db.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                Document.processing_status != "PROCESSING",
            )
            .values(
                processing_status="PROCESSING",
                processing_error=None,
                updated_at=datetime.now(UTC),
            )
            .returning(Document.id)
        )
        return result.scalar_one_or_none() is not None

    def mark_processing(self, document: Document) -> None:
        document.processing_status = "PROCESSING"
        document.processing_error = None
        document.updated_at = datetime.now(UTC)
        self.db.add(document)
        self.db.flush()

    def mark_ready(
        self,
        document: Document,
        *,
        preview_storage_key: str | None,
        preview_page_count: int,
    ) -> None:
        document.processing_status = "READY"
        document.processing_error = None
        document.preview_storage_key = preview_storage_key
        document.preview_page_count = preview_page_count
        document.updated_at = datetime.now(UTC)
        self.db.add(document)
        self.db.flush()

    def mark_failed(self, document: Document, error: str) -> None:
        document.processing_status = "FAILED"
        document.processing_error = error[:4000]
        document.updated_at = datetime.now(UTC)
        self.db.add(document)
        self.db.flush()

    def replace_pages(
        self, document_id: UUID, pages: list[ExtractedPage]
    ) -> None:
        """Rebuild pages for one document only (idempotent reprocess)."""
        self.db.execute(
            delete(DocumentPage).where(DocumentPage.document_id == document_id)
        )
        for page in pages:
            self.db.add(
                DocumentPage(
                    document_id=document_id,
                    page_no=page.page_no,
                    extracted_text=page.extracted_text,
                    layout_json=page.layout_json,
                    extraction_method=page.extraction_method,
                )
            )
        self.db.flush()

    def list_pages(self, document_id: UUID) -> list[DocumentPage]:
        return list(
            self.db.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_no.asc())
            )
            .scalars()
            .all()
        )

    def count_pages(self, document_id: UUID) -> int:
        from sqlalchemy import func

        return int(
            self.db.execute(
                select(func.count())
                .select_from(DocumentPage)
                .where(DocumentPage.document_id == document_id)
            ).scalar_one()
        )
