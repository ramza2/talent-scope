"""V41-D: PostgreSQL execution ownership and attempt fencing (no schema changes).

A shared session advisory lock lives on a separate, detached connection for
the lifetime of a Celery analysis task. Recovery takes the matching exclusive
transaction advisory lock. A lost connection can release ownership; the
started_at fence at every analysis write is therefore independently required.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from hashlib import blake2b
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.session import engine


def execution_key(run_id: UUID) -> int:
    """Stable, namespaced signed bigint; a hash collision only blocks recovery."""
    return int.from_bytes(
        blake2b(run_id.bytes, digest_size=8, person=b"ts-analysis-v41").digest(),
        "big",
        signed=True,
    )


@contextmanager
def worker_execution_guard(run_id: UUID) -> Iterator[None]:
    # Never return a session holding an advisory lock to the connection pool.
    # AUTOCOMMIT avoids a long-lived idle transaction during GPU calls.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.detach()
        conn.execute(
            text("SELECT pg_advisory_lock_shared(:key)"),
            {"key": execution_key(run_id)},
        )
        yield
    # The detached physical connection closes even on exceptions.


def try_recovery_guard(db: Session, run_id: UUID) -> bool:
    """Exclusive lock automatically releases on commit/rollback."""
    return db.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": execution_key(run_id)},
    ).scalar_one() is True


def worker_guard_held(db: Session, run_id: UUID) -> bool:
    """Read-only observation for UI diagnostics; POST must take the lock."""
    key = execution_key(run_id) & ((1 << 64) - 1)
    return db.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks "
            "WHERE locktype = 'advisory' AND granted AND mode = 'ShareLock' "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND classid::bigint = :hi AND objid::bigint = :lo AND objsubid = 1)"
        ),
        {"hi": key >> 32, "lo": key & 0xffffffff},
    ).scalar_one() is True


class AnalysisAttemptSuperseded(Exception):
    def __init__(self, status: str) -> None:
        super().__init__("Analysis execution attempt is no longer current")
        self.status = status


def lock_current_attempt(
    db: Session, run_id: UUID, expected_started_at: datetime,
) -> AnalysisRun:
    """Refresh under row lock, never let a cached ORM instance authorize writes."""
    run = db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise AnalysisAttemptSuperseded("NOT_FOUND")
    if (
        expected_started_at is None
        or run.status != "PROCESSING"
        or run.started_at != expected_started_at
    ):
        raise AnalysisAttemptSuperseded(run.status)
    return run
