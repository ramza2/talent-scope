"""V41: conservative detection and administrator recovery of orphaned analyses.

No background task changes analysis status. Recovery is explicitly requested,
requires a responsive single-worker Celery snapshot twice, and changes one run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.exceptions import AnalysisStateConflictError, NotFoundError
from app.db.models.analysis import AnalysisRun
from app.modules.analysis.repository import AnalysisRepository
from app.tasks.celery_app import celery_app

# Conservative threshold: a long but active analysis is NEVER a recovery target.
STALE_AFTER = timedelta(minutes=30)
MAX_CANDIDATES = 50
RECOVERY_ERROR = (
    "AI 분석 실행이 중단된 것으로 확인되어 관리자가 실패 처리했습니다. "
    "AI 서비스 상태 확인 후 다시 분석해 주세요."
)


@dataclass(frozen=True)
class WorkerSnapshot:
    """An all-or-nothing snapshot from exactly one responding Celery worker."""

    worker_name: str | None
    requests: tuple[str, ...]
    healthy: bool
    reason: str

    def target_state(self, analysis_id: UUID) -> str:
        if not self.healthy:
            return "UNKNOWN"
        needle = str(analysis_id)
        return "ACTIVE" if any(needle in request for request in self.requests) else "ABSENT"


def inspect_worker(*, app: Any = None, timeout: float = 4.0) -> WorkerSnapshot:
    """Fail closed on no reply, malformed data, or a different worker set.

    v41 intentionally supports only a single Celery worker. With multiple or
    missing workers, an absent task is not sufficient evidence of orphaning.
    """
    app = app or celery_app
    try:
        inspector = app.control.inspect(timeout=timeout)
        names: list[str] = []
        requests: list[str] = []
        for category in ("ping", "active", "reserved", "scheduled"):
            result = getattr(inspector, category)()
            if not isinstance(result, dict) or len(result) != 1:
                return WorkerSnapshot(None, (), False, "WORKER_REPLY_UNAVAILABLE")
            name, payload = next(iter(result.items()))
            if not isinstance(name, str) or not name:
                return WorkerSnapshot(None, (), False, "WORKER_NAME_INVALID")
            names.append(name)
            if category == "ping":
                if not isinstance(payload, dict) or payload.get("ok") != "pong":
                    return WorkerSnapshot(None, (), False, "WORKER_PING_UNHEALTHY")
                continue
            if not isinstance(payload, list):
                return WorkerSnapshot(None, (), False, "TASK_LIST_INVALID")
            for item in payload:
                if not isinstance(item, dict):
                    return WorkerSnapshot(None, (), False, "TASK_REQUEST_INVALID")
                request = item.get("request", item)
                if not isinstance(request, dict):
                    return WorkerSnapshot(None, (), False, "TASK_REQUEST_INVALID")
                if "args" not in request and "kwargs" not in request:
                    return WorkerSnapshot(None, (), False, "TASK_ARGUMENTS_UNKNOWN")
                # Only compare task identifiers; never send request payload to UI/logs.
                requests.append(str(request))
        if len(set(names)) != 1:
            return WorkerSnapshot(None, (), False, "WORKER_SET_CHANGED")
        return WorkerSnapshot(names[0], tuple(requests), True, "OK")
    except Exception:
        # Do not leak broker URLs, credentials, or task arguments via errors.
        return WorkerSnapshot(None, (), False, "WORKER_INSPECTION_FAILED")


def _is_stale(run: AnalysisRun, now: datetime) -> bool:
    if (
        run.status != "PROCESSING"
        or run.started_at is None
        or run.completed_at is not None
        or bool(run.error_message)
    ):
        return False
    # updated_at may advance when VLM transcription is saved; avoid premature action.
    last_activity = max(run.started_at, run.updated_at or run.started_at)
    return last_activity <= now - STALE_AFTER


def list_stale_candidates(
    db: Session, *, now: datetime | None = None, limit: int = MAX_CANDIDATES
) -> list[AnalysisRun]:
    now = now or datetime.now(UTC)
    cutoff = now - STALE_AFTER
    return list(
        db.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.status == "PROCESSING",
                AnalysisRun.started_at.is_not(None),
                AnalysisRun.started_at <= cutoff,
                AnalysisRun.updated_at <= cutoff,
                AnalysisRun.completed_at.is_(None),
                AnalysisRun.error_message.is_(None),
            )
            .order_by(AnalysisRun.started_at.asc(), AnalysisRun.id.asc())
            .limit(max(1, min(int(limit), MAX_CANDIDATES)))
        ).scalars().all()
    )


def evaluate_analysis(db: Session, analysis_id: UUID) -> dict[str, Any]:
    """Read-only check. UNKNOWN must never be interpreted as safe to recover."""
    run = db.get(AnalysisRun, analysis_id)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")
    stale = _is_stale(run, datetime.now(UTC))
    snapshot = inspect_worker() if stale else None
    state = snapshot.target_state(analysis_id) if snapshot is not None else "NOT_CHECKED"
    return {
        "analysis_id": analysis_id,
        "status": run.status,
        "started_at": run.started_at,
        "updated_at": run.updated_at,
        "stale": stale,
        "worker_task_state": state,
        "recovery_allowed": stale and state == "ABSENT",
        "reason": snapshot.reason if snapshot is not None else "NOT_STALE_OR_NOT_PROCESSING",
    }


def recover_orphan(
    db: Session,
    analysis_id: UUID,
    actor_user_id: UUID,
) -> dict[str, Any]:
    """One-run administrative recovery; does not revoke/requeue any Celery task.

    All DB changes (failed transition and audit) are in one transaction.
    A stale claim is necessary but not sufficient: two healthy worker snapshots
    must both report the target task absent, including while the DB row is locked.
    """
    # Never acquire a write lock when the precondition is already false.
    peek = db.get(AnalysisRun, analysis_id)
    if peek is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")
    if not _is_stale(peek, datetime.now(UTC)):
        raise AnalysisStateConflictError("복구 가능한 오래된 PROCESSING 분석이 아닙니다.")

    first = inspect_worker()
    if first.target_state(analysis_id) != "ABSENT":
        raise AnalysisStateConflictError("Worker 실행 여부가 불확실하거나 분석 작업이 살아 있어 복구하지 않습니다.")

    try:
        # PostgreSQL transaction-local limits; never modify schema or other rows.
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
        db.execute(text("SET LOCAL statement_timeout = '90s'"))
        # Identity-map safety: re-read post-lock state, not the earlier peek.
        db.expire(peek)
        repo = AnalysisRepository(db)
        run = repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        if not _is_stale(run, datetime.now(UTC)):
            raise AnalysisStateConflictError("분석 상태가 변경되어 복구를 중단합니다.")

        second = inspect_worker()
        if second.target_state(analysis_id) != "ABSENT":
            raise AnalysisStateConflictError("잠금 중 Worker 작업이 감지되어 복구를 중단합니다.")
        if second.worker_name != first.worker_name:
            raise AnalysisStateConflictError("Worker가 변경되어 복구를 중단합니다.")

        repo.mark_failed(run, RECOVERY_ERROR)
        repo.add_audit(
            action_type="ANALYSIS_FAILED",
            actor_user_id=actor_user_id,
            target_id=analysis_id,
            before={"status": "PROCESSING"},
            after={"status": "FAILED", "reason": "orphaned_worker_task"},
            metadata={
                "reason": "V41_ADMIN_ORPHAN_RECOVERY",
                "person_id": str(run.person_id),
            },
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    return {"analysis_id": analysis_id, "status": "FAILED", "recovered": True}
