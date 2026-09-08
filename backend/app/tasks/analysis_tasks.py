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
