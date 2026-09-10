"""Index queue tasks — SearchIndexJob dispatcher + REBUILD_PERSON worker."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.db.session import SessionLocal
from app.modules.search.repository import SearchRepository
from app.modules.search.service import SearchIndexService
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.index_tasks.process_search_index_job")
def process_search_index_job(job_id: str) -> dict[str, Any]:
    """Process one SearchIndexJob using a dedicated SQLAlchemy session."""
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
    finally:
        db.close()


@celery_app.task(name="app.tasks.index_tasks.dispatch_pending_search_index_jobs")
def dispatch_pending_search_index_jobs(limit: int = 50) -> dict[str, Any]:
    """Publish PENDING SearchIndexJob ids without mutating status.

    Business transactions only insert SearchIndexJob rows. This dispatcher is the
    sole Celery publish path so Confirm/update TX never calls .delay().
    Duplicate publishes are safe thanks to atomic claim in process_search_index_job.
    """
    db = SessionLocal()
    try:
        repo = SearchRepository(db)
        job_ids = repo.list_pending_job_ids(limit=max(1, min(int(limit), 500)))
    finally:
        db.close()

    published: list[str] = []
    for job_id in job_ids:
        process_search_index_job.delay(str(job_id))
        published.append(str(job_id))

    logger.info(
        "search_index dispatcher published=%s limit=%s",
        len(published),
        limit,
    )
    return {"published": len(published), "job_ids": published}
