"""Document convert / extract / page-index Celery tasks."""

from __future__ import annotations

import logging
from uuid import UUID

from app.db.session import SessionLocal
from app.modules.document_processing.service import DocumentProcessingService
from app.storage.s3 import get_object_storage
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.document_tasks.process_document",
    bind=True,
    max_retries=0,
)
def process_document(self, document_id: str) -> dict[str, str]:
    """Convert/extract one Document into READY DocumentPages.

    Uses its own SQLAlchemy session (no FastAPI request dependencies).
    """
    db = SessionLocal()
    try:
        service = DocumentProcessingService(
            db, storage=get_object_storage()
        )
        status = service.process_document(UUID(document_id))
        return {"status": status, "document_id": document_id}
    except Exception:
        logger.exception("process_document task failed document_id=%s", document_id)
        raise
    finally:
        db.close()


def enqueue_document_processing(document_id: UUID) -> None:
    """Best-effort enqueue after DB commit. Failures must not roll back Documents."""
    try:
        process_document.delay(str(document_id))
    except Exception:
        logger.warning(
            "failed to enqueue process_document document_id=%s",
            document_id,
            exc_info=True,
        )
