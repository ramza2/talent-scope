"""Document convert / extract / page-index Celery tasks."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.modules.document_processing.service import DocumentProcessingService
from app.storage.s3 import get_object_storage
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _parse_uuid_list(values: Sequence[str] | None) -> list[UUID] | None:
    if values is None:
        return None
    return [UUID(str(v)) for v in values]


def _maybe_start_auto_profile_analysis(
    db: Session,
    document_id: UUID,
    *,
    batch_document_ids: Sequence[UUID] | None = None,
    new_document_ids: Sequence[UUID] | None = None,
) -> None:
    """Best-effort PROFILE analysis after READY. Never fails document processing.

    On active-run conflict the analysis service persists a DEFERRED AnalysisRun
    and schedules promotion retries by ``analysis_run_id``.
    """
    batch = list(batch_document_ids) if batch_document_ids is not None else [document_id]
    new_ids = (
        list(new_document_ids) if new_document_ids is not None else [document_id]
    )
    try:
        from app.modules.analysis.service import AnalysisService

        AnalysisService(
            db, storage=get_object_storage()
        ).create_analysis_for_ready_documents(
            batch,
            new_document_ids=new_ids,
        )
    except Exception:
        logger.exception(
            "auto analysis failed document_id=%s batch=%s",
            document_id,
            [str(i) for i in batch],
        )


@celery_app.task(
    name="app.tasks.document_tasks.process_document",
    bind=True,
    max_retries=0,
)
def process_document(
    self,
    document_id: str,
    batch_document_ids: list[str] | None = None,
    new_document_ids: list[str] | None = None,
) -> dict[str, str]:
    """Convert/extract one Document into READY DocumentPages.

    Uses its own SQLAlchemy session (no FastAPI request dependencies).
    On READY, attempts idempotent auto PROFILE analysis for the upload batch
    without affecting document processing outcome.

    ``batch_document_ids`` / ``new_document_ids`` are optional for backward
    compatibility with older ``process_document.delay(document_id)`` callers.
    """
    db = SessionLocal()
    try:
        service = DocumentProcessingService(
            db, storage=get_object_storage()
        )
        status = service.process_document(UUID(document_id))
        if status == "READY":
            _maybe_start_auto_profile_analysis(
                db,
                UUID(document_id),
                batch_document_ids=_parse_uuid_list(batch_document_ids),
                new_document_ids=_parse_uuid_list(new_document_ids),
            )
        return {"status": status, "document_id": document_id}
    except Exception:
        logger.exception("process_document task failed document_id=%s", document_id)
        raise
    finally:
        db.close()


def enqueue_document_processing(
    document_id: UUID,
    *,
    batch_document_ids: Sequence[UUID] | None = None,
    new_document_ids: Sequence[UUID] | None = None,
) -> None:
    """Best-effort enqueue after DB commit. Failures must not roll back Documents."""
    try:
        kwargs: dict[str, list[str]] = {}
        if batch_document_ids is not None:
            kwargs["batch_document_ids"] = [str(i) for i in batch_document_ids]
        if new_document_ids is not None:
            kwargs["new_document_ids"] = [str(i) for i in new_document_ids]
        process_document.delay(str(document_id), **kwargs)
    except Exception:
        logger.warning(
            "failed to enqueue process_document document_id=%s",
            document_id,
            exc_info=True,
        )
