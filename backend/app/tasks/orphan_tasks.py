"""V41 periodic read-only detection; never mutates analysis status."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis.orphan_recovery import inspect_worker, list_stale_candidates
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.orphan_tasks.report_stale_analysis_runs",
    max_retries=0,
    ignore_result=True,
)
def report_stale_analysis_runs() -> dict[str, int | str]:
    """Log aggregate counts only; explicit administrator action is required to fix."""
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '10s'"))
        candidates = list_stale_candidates(db)
        db.rollback()

    snapshot = inspect_worker() if candidates else None
    active = sum(snapshot.target_state(r.id) == "ACTIVE" for r in candidates) if snapshot else 0
    absent = sum(snapshot.target_state(r.id) == "ABSENT" for r in candidates) if snapshot else 0
    unknown = len(candidates) - active - absent
    if candidates:
        logger.warning(
            "analysis orphan monitor stale=%d active=%d absent=%d unknown=%d",
            len(candidates), active, absent, unknown,
        )
    return {
        "stale": len(candidates),
        "active": active,
        "absent": absent,
        "unknown": unknown,
        "worker_state": snapshot.reason if snapshot else "NO_CANDIDATES",
    }
