"""Analysis / identify Celery tasks.

Redis is broker only — DB row status is the source of truth.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from app.core.exceptions import AnalysisStateConflictError
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
    """Retry auto PROFILE analysis after an unrelated active run clears.

    Self-schedules with a fixed countdown while ``AnalysisStateConflictError``
    persists, up to ``AUTO_ANALYSIS_DEFER_MAX_ATTEMPTS``. Never creates a FAILED
    AnalysisRun on exhaustion.
    """
    db = SessionLocal()
    try:
        batch = [UUID(str(i)) for i in batch_document_ids]
        new_ids = [UUID(str(i)) for i in new_document_ids]
        service = AnalysisService(db, storage=get_object_storage())
        try:
            result = service.create_analysis_for_ready_documents(
                batch,
                new_document_ids=new_ids,
            )
        except AnalysisStateConflictError:
            db.rollback()
            if int(attempt) >= AUTO_ANALYSIS_DEFER_MAX_ATTEMPTS:
                logger.warning(
                    "auto analysis defer exhausted batch=%s new=%s attempt=%s",
                    batch_document_ids,
                    new_document_ids,
                    attempt,
                )
                return {"status": "DEFER_EXHAUSTED", "attempt": str(attempt)}
            next_attempt = int(attempt) + 1
            retry_auto_profile_analysis_batch.apply_async(
                kwargs={
                    "batch_document_ids": [str(i) for i in batch],
                    "new_document_ids": [str(i) for i in new_ids],
                    "attempt": next_attempt,
                },
                countdown=AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS,
            )
            logger.info(
                "auto analysis deferred batch=%s attempt=%s next_attempt=%s "
                "countdown=%s",
                batch_document_ids,
                attempt,
                next_attempt,
                AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS,
            )
            return {
                "status": "DEFERRED",
                "attempt": str(attempt),
                "next_attempt": str(next_attempt),
            }

        if result is None:
            logger.info(
                "auto analysis defer skipped batch=%s attempt=%s",
                batch_document_ids,
                attempt,
            )
            return {"status": "SKIPPED", "attempt": str(attempt)}
        return {
            "status": "QUEUED",
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
    batch_document_ids: Sequence[UUID],
    new_document_ids: Sequence[UUID],
    *,
    attempt: int = 1,
) -> None:
    """Schedule first/next deferred auto-analysis attempt (broker-only)."""
    retry_auto_profile_analysis_batch.apply_async(
        kwargs={
            "batch_document_ids": [str(i) for i in batch_document_ids],
            "new_document_ids": [str(i) for i in new_document_ids],
            "attempt": int(attempt),
        },
        countdown=AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS,
    )
