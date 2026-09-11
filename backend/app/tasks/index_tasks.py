"""Index queue tasks — SearchIndexJob dispatcher + REBUILD_PERSON worker."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.modules.search.errors import (
    SearchIndexProcessingError,
    SearchIndexTaskError,
    safe_search_job_error,
)
from app.modules.search.service import SearchIndexService
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.index_tasks.process_search_index_job")
def process_search_index_job(job_id: str) -> dict[str, Any]:
    """Process one SearchIndexJob using a dedicated SQLAlchemy session.

    Celery boundary never re-raises the original exception (SQL/params/PII).
    """
    db = SessionLocal()
    try:
        service = SearchIndexService(db)
        result = service.process_job(UUID(str(job_id)))
        return {
            "job_id": str(result.job_id),
            "status": result.status,
            "claimed": result.claimed,
            "profile_document_count": result.profile_document_count,
            "project_document_count": result.project_document_count,
            "upserted_count": result.upserted_count,
            "deactivated_count": result.deactivated_count,
        }
    except SearchIndexProcessingError as exc:
        logger.error(
            "search_index task failed job_id=%s exc_class=%s error=%s",
            job_id,
            type(exc).__name__,
            str(exc),
        )
        raise SearchIndexTaskError("search index processing failed") from None
    except Exception as exc:
        safe = safe_search_job_error(exc)
        logger.error(
            "search_index task failed job_id=%s exc_class=%s error=%s",
            job_id,
            type(exc).__name__,
            safe,
        )
        raise SearchIndexTaskError("search index processing failed") from None
    finally:
        db.close()


@celery_app.task(name="app.tasks.index_tasks.dispatch_pending_search_index_jobs")
def dispatch_pending_search_index_jobs(limit: int = 50) -> dict[str, Any]:
    """Reserve PENDING jobs (PROCESSING + commit) then publish Celery tasks.

    PROCESSING means "dispatcher reserved; not yet terminal". Duplicate dispatch
    cycles do not re-publish the same job while it remains PROCESSING.
    """
    db = SessionLocal()
    reserved_ids: list[str] = []
    published: list[str] = []
    restored: list[str] = []
    try:
        service = SearchIndexService(db)
        reserved = service.reserve_pending_jobs(limit=max(1, min(int(limit), 500)))
        reserved_ids = [str(job.id) for job in reserved]

        for job in reserved:
            job_id = str(job.id)
            try:
                process_search_index_job.delay(job_id)
                published.append(job_id)
            except Exception as exc:
                logger.warning(
                    "search_index publish failed job_id=%s exc_class=%s",
                    job_id,
                    type(exc).__name__,
                )
                if service.release_reserved_job(job.id):
                    restored.append(job_id)
    finally:
        db.close()

    logger.info(
        "search_index dispatcher reserved=%s published=%s restored=%s limit=%s",
        len(reserved_ids),
        len(published),
        len(restored),
        limit,
    )
    return {
        "reserved": len(reserved_ids),
        "published": len(published),
        "restored": len(restored),
        "job_ids": published,
        "restored_job_ids": restored,
    }


@celery_app.task(name="app.tasks.index_tasks.recover_stale_search_index_jobs")
def recover_stale_search_index_jobs(limit: int = 100) -> dict[str, Any]:
    """Return stuck PROCESSING jobs to PENDING. Does not rebuild or publish."""
    db = SessionLocal()
    try:
        service = SearchIndexService(db)
        recovered = service.recover_stale_processing_jobs(
            limit=max(1, min(int(limit), 500))
        )
    finally:
        db.close()

    recovered_ids = [str(job_id) for job_id in recovered]
    logger.info(
        "search_index stale recovery count=%s job_ids=%s",
        len(recovered_ids),
        recovered_ids[:20],
    )
    return {"recovered": len(recovered_ids), "job_ids": recovered_ids}


@celery_app.task(name="app.tasks.index_tasks.enqueue_missing_search_embeddings")
def enqueue_missing_search_embeddings(limit: int = 100) -> dict[str, Any]:
    """Ensure PENDING Embedding UPSERT jobs for missing/outdated PROFILE/PROJECT rows.

    Does not call the embedding provider and does not Celery-publish process tasks.
    """
    db = SessionLocal()
    try:
        service = SearchIndexService(db)
        result = service.enqueue_missing_embeddings(limit=max(1, min(int(limit), 500)))
    finally:
        db.close()
    logger.info(
        "search_index embedding enqueue scanned=%s enqueued=%s",
        result.get("scanned"),
        result.get("enqueued"),
    )
    return result

