"""Analysis / identify Celery tasks.

Redis is broker only — DB row status is the source of truth.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.db.session import SessionLocal
from app.modules.analysis.service import AnalysisService
from app.modules.upload_identification.service import IdentificationService
from app.storage.s3 import get_object_storage
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Bounded deferred auto-analysis after an unrelated active run blocks create.
AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS = 30
AUTO_ANALYSIS_DEFER_MAX_ATTEMPTS = 20


@celery_app.task(
    name="app.tasks.analysis_tasks.identify_upload_session",
    bind=True,
    max_retries=0,
)
def identify_upload_session(
    self,
    upload_session_id: str,
    actor_user_id: str | None = None,
) -> dict[str, str]:
    """Run temp-file identity extraction for an UploadSession."""
    db = SessionLocal()
    try:
        actor = UUID(actor_user_id) if actor_user_id else None
        service = IdentificationService(db, storage=get_object_storage())
        status = service.run(UUID(upload_session_id), actor_user_id=actor)
        return {"status": status, "upload_session_id": upload_session_id}
    except Exception:
        logger.exception(
            "identify_upload_session task failed upload_session_id=%s",
            upload_session_id,
        )
        raise
    finally:
        db.close()


def enqueue_upload_identify(
    upload_session_id: UUID, actor_user_id: UUID | None = None
) -> None:
    """Enqueue identify task. Raises on broker failure (caller restores status)."""
    identify_upload_session.delay(
        str(upload_session_id),
        str(actor_user_id) if actor_user_id is not None else None,
    )


@celery_app.task(
    name="app.tasks.analysis_tasks.run_profile_analysis",
    bind=True,
    max_retries=0,
)
def run_profile_analysis(
    self,
    analysis_run_id: str,
    actor_user_id: str | None = None,
) -> dict[str, str]:
    """Run detailed profile analysis for an AnalysisRun."""
    db = SessionLocal()
    try:
        actor = UUID(actor_user_id) if actor_user_id else None
        service = AnalysisService(db, storage=get_object_storage())
        status = service.run_analysis(UUID(analysis_run_id), actor_user_id=actor)
        return {"status": status, "analysis_run_id": analysis_run_id}
    except Exception:
        logger.exception(
            "run_profile_analysis task failed analysis_run_id=%s",
            analysis_run_id,
        )
        raise
    finally:
        db.close()


def enqueue_profile_analysis(
    analysis_run_id: UUID, actor_user_id: UUID | None = None
) -> None:
    """Enqueue profile analysis. Raises on broker failure (caller marks FAILED)."""
    run_profile_analysis.delay(
        str(analysis_run_id),
        str(actor_user_id) if actor_user_id is not None else None,
    )


@celery_app.task(
    name="app.tasks.analysis_tasks.retry_deferred_auto_profile_analysis",
    bind=True,
    max_retries=0,
)
def retry_deferred_auto_profile_analysis(
    self,
    analysis_run_id: str,
    attempt: int = 1,
) -> dict[str, str]:
    """Promote an existing DEFERRED AnalysisRun, or re-schedule while blocked.

    Reuses the same ``analysis_run_id``; never creates a new run.
    Exhaustion and broker failures are handled inside
    ``AnalysisService.promote_deferred_analysis`` (DEFERRED → FAILED).
    """
    db = SessionLocal()
    try:
        service = AnalysisService(db, storage=get_object_storage())
        status = service.promote_deferred_analysis(
            UUID(analysis_run_id),
            attempt=int(attempt),
        )
        return {
            "status": status,
            "analysis_run_id": analysis_run_id,
            "attempt": str(attempt),
        }
    except Exception:
        logger.exception(
            "retry_deferred_auto_profile_analysis failed analysis_run_id=%s "
            "attempt=%s",
            analysis_run_id,
            attempt,
        )
        raise
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.analysis_tasks.retry_auto_profile_analysis_batch",
    bind=True,
    max_retries=0,
)
def retry_auto_profile_analysis_batch(
    self,
    batch_document_ids: list[str],
    new_document_ids: list[str],
    attempt: int = 1,
) -> dict[str, str]:
    """Compatibility task for pre-DEFERRED ETA messages still in Redis.

    Old workers scheduled this name with batch document ids. New workers must
    keep the Celery registry entry so those messages are not dropped as
    unregistered. Execution delegates to ``create_analysis_for_ready_documents``
    which persists DEFERRED (and schedules promote by run id) or creates QUEUED.
    """
    db = SessionLocal()
    try:
        batch = [UUID(str(i)) for i in batch_document_ids]
        new_ids = [UUID(str(i)) for i in new_document_ids]
        service = AnalysisService(db, storage=get_object_storage())
        result = service.create_analysis_for_ready_documents(
            batch,
            new_document_ids=new_ids,
        )
        if result is None:
            logger.info(
                "compat auto analysis batch skipped batch=%s attempt=%s",
                batch_document_ids,
                attempt,
            )
            return {"status": "SKIPPED", "attempt": str(attempt)}
        return {
            "status": result.status,
            "analysis_run_id": str(result.analysis_id),
            "attempt": str(attempt),
        }
    except Exception:
        logger.exception(
            "retry_auto_profile_analysis_batch failed batch=%s attempt=%s",
            batch_document_ids,
            attempt,
        )
        raise
    finally:
        db.close()


def enqueue_deferred_auto_profile_analysis(
    analysis_run_id: UUID,
    *,
    attempt: int = 1,
) -> None:
    """Schedule first/next deferred promotion attempt by analysis_run_id."""
    retry_deferred_auto_profile_analysis.apply_async(
        kwargs={
            "analysis_run_id": str(analysis_run_id),
            "attempt": int(attempt),
        },
        countdown=AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS,
    )
