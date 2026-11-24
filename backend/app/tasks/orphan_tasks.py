"""V41 periodic read-only detection; never mutates analysis status."""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis.execution_guard import worker_guard_held
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
        # Rollback expires ORM objects even with expire_on_commit=False.
        # Keep only immutable IDs before closing the read-only session.
        candidate_ids = [run.id for run in list_stale_candidates(db)]
        owned = {run_id for run_id in candidate_ids if worker_guard_held(db, run_id)}
        db.rollback()

    snapshot = inspect_worker() if candidate_ids else None
    states = [
        "ACTIVE" if run_id in owned else snapshot.target_state(run_id)
        for run_id in candidate_ids
    ] if snapshot else []
    active = states.count("ACTIVE")
    absent = states.count("ABSENT")
    unknown = len(candidate_ids) - active - absent
    if candidate_ids:
        logger.warning(
            "analysis orphan monitor stale=%d active=%d absent=%d unknown=%d",
            len(candidate_ids), active, absent, unknown,
        )
    return {
        "stale": len(candidate_ids),
        "active": active,
        "absent": absent,
        "unknown": unknown,
        "worker_state": snapshot.reason if snapshot else "NO_CANDIDATES",
    }
