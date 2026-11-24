"""V41-D integration: real isolated PG and Celery/Redis, no real AI or storage.

Run worker with -A scripts.verify_v41d_execution_guard:celery_app. The fixture
invokes the actual run_profile_analysis task wrapper with a blocking fake
service; the production execution guard and real Celery inspect are exercised.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

assert os.environ.get("V41_ISOLATED_TEST") == "1"
assert os.environ.get("APP_ENV") == "test"
assert os.environ.get("DATABASE_URL") == (
    "postgresql+psycopg://v41check:v41-isolated-only@v41-db:5432/v41_isolated"
)
assert os.environ.get("REDIS_URL") == "redis://v41-redis:6379/0"

from redis import Redis
from sqlalchemy import select, text

from app.core.exceptions import AnalysisStateConflictError
from app.db.models.analysis import AnalysisRun
from app.db.models.person import Person
from app.db.models.revision import AuditLog
from app.db.models.user import AppUser
from app.db.session import SessionLocal
from app.modules.analysis import orphan_recovery as recovery
from app.modules.analysis.execution_guard import (
    AnalysisAttemptSuperseded, lock_current_attempt, try_recovery_guard,
    worker_guard_held, worker_execution_guard,
)
from app.modules.analysis.service import AnalysisService
from app.tasks import analysis_tasks, orphan_tasks
from app.tasks.celery_app import celery_app


def signal_key(run_id, suffix):
    return f"v41d-fixture:{run_id}:{suffix}"


@celery_app.task(name="v41d.fixture_hold")
def fixture_hold(run_id):
    class FakeAnalysis:
        def __init__(self, db, **kwargs):
            self.db = db

        def run_analysis(self, analysis_id, **kwargs):
            # This runs inside the real production task wrapper.
            assert worker_guard_held(self.db, analysis_id)
            self.db.rollback()
            client = Redis.from_url(os.environ["REDIS_URL"])
            client.set(signal_key(run_id, "ready"), "1", ex=120)
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                if client.get(signal_key(run_id, "release")):
                    return "FIXTURE_RELEASED"
                time.sleep(0.1)
            raise RuntimeError("fixture hold timed out")

    with patch.object(analysis_tasks, "AnalysisService", FakeAnalysis), patch.object(
        analysis_tasks, "get_object_storage", return_value=object()
    ):
        return analysis_tasks.run_profile_analysis.run(run_id)


def check_absence_without_worker():
    snapshot = recovery.inspect_worker(timeout=0.75)
    assert snapshot.target_state(uuid4()) == "UNKNOWN"
    print("V41D_REAL_CELERY_NO_WORKER_FAIL_CLOSED_PASS", flush=True)


def seed_run(actor, *, status="PROCESSING", started_at=None):
    person_id, run_id = uuid4(), uuid4()
    with SessionLocal() as db:
        db.add(Person(id=person_id, created_by=actor))
        db.flush()
        db.add(AnalysisRun(
            id=run_id, person_id=person_id, status=status,
            started_at=started_at, updated_at=started_at or datetime.now(UTC),
            candidate_json={"fixture": "unchanged"},
        ))
        db.commit()
    return run_id


def snapshot_state(run_id):
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        audits = db.execute(select(AuditLog).where(AuditLog.target_id == run_id)).scalars().all()
        return (run.status, run.started_at, run.candidate_json, run.error_message,
                tuple((a.action_type, a.after_json) for a in audits))


def must_block(call, error_type):
    try:
        call()
    except error_type:
        return
    raise AssertionError("unsafe operation unexpectedly proceeded")


def main():
    with SessionLocal() as db:
        assert db.execute(text("SELECT current_database(), current_user")).one() == (
            "v41_isolated", "v41check"
        )
        actor = uuid4()
        db.add(AppUser(id=actor, login_id="v41d-synthetic-" + actor.hex,
                       password_hash="unused", name="Synthetic", role="ADMIN"))
        db.commit()

    old = datetime.now(UTC) - timedelta(minutes=80)
    run_id = seed_run(actor, started_at=old)
    other_id = seed_run(actor, started_at=old)
    before = snapshot_state(run_id)
    # Wait only for the isolated worker's real remote-control readiness.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready = celery_app.control.inspect(timeout=1.0).ping()
        if isinstance(ready, dict) and len(ready) == 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("isolated worker did not become ready")

    client = Redis.from_url(os.environ["REDIS_URL"])
    task = fixture_hold.apply_async(args=[str(run_id)], queue="v41d-fixture")
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.get(signal_key(run_id, "ready")):
                break
            if task.ready():
                raise AssertionError("fixture stopped before owning the guard")
            time.sleep(0.1)
        else:
            raise AssertionError("fixture never acquired execution guard")
        real = recovery.inspect_worker(timeout=1.5)
        assert real.healthy and real.target_state(run_id) == "ACTIVE", real.reason
        assert real.target_state(other_id) == "ABSENT"
        with SessionLocal() as db:
            assert worker_guard_held(db, run_id)
            assert not try_recovery_guard(db, run_id)
            db.rollback()
        print("V41D_REAL_CELERY_ACTIVE_ABSENT_AND_WORKER_GUARD_PASS", flush=True)

        # Simulate a false-negative Celery response while the real worker owns
        # the database execution guard. Both GET and POST must be fail-closed.
        absent = recovery.WorkerSnapshot(real.worker_name, (), True, "OK")
        with patch.object(recovery, "inspect_worker", return_value=absent):
            with SessionLocal() as db:
                check = recovery.evaluate_analysis(db, run_id)
                assert check["worker_task_state"] == "ACTIVE"
                assert check["recovery_allowed"] is False
                must_block(lambda: recovery.recover_orphan(db, run_id, actor),
                           AnalysisStateConflictError)
        with patch.object(orphan_tasks, "inspect_worker", return_value=absent):
            report = orphan_tasks.report_stale_analysis_runs.run()
            assert report["active"] >= 1
        assert snapshot_state(run_id) == before
        print("V41D_FALSE_NEGATIVE_RECOVERY_BLOCKED_REAL_DB_PASS", flush=True)
    finally:
        client.set(signal_key(run_id, "release"), "1", ex=120)
        result = task.get(timeout=25, propagate=True)
        assert result == {"status": "FIXTURE_RELEASED", "analysis_run_id": str(run_id)}

    with SessionLocal() as db:
        assert not worker_guard_held(db, run_id)
        assert try_recovery_guard(db, run_id)
        db.rollback()
    # Even an exceptional exit must close the detached guard connection.
    try:
        with worker_execution_guard(other_id):
            with SessionLocal() as db:
                assert worker_guard_held(db, other_id)
            raise RuntimeError("synthetic guard exception")
    except RuntimeError as exc:
        assert str(exc) == "synthetic guard exception"
    with SessionLocal() as db:
        assert not worker_guard_held(db, other_id)
        assert try_recovery_guard(db, other_id)
        db.rollback()
    print("V41D_EXECUTION_GUARD_RELEASE_PASS", flush=True)

    # Hold a cached old ORM instance, then simulate a newer claimed attempt.
    # No new job is submitted; only the disposable DB fixture is changed.
    with SessionLocal() as stale_db:
        cached = stale_db.get(AnalysisRun, other_id)
        assert cached.started_at == old
        newer = old + timedelta(minutes=5)
        with SessionLocal() as db:
            row = db.get(AnalysisRun, other_id)
            row.started_at = newer
            row.candidate_json = {"fixture": "new_attempt"}
            db.commit()
        expected = snapshot_state(other_id)
        must_block(lambda: lock_current_attempt(stale_db, other_id, old),
                   AnalysisAttemptSuperseded)
        stale_db.rollback()
        service = AnalysisService(stale_db, storage=object())
        # Sentinel values must never be dereferenced by stale write paths.
        must_block(lambda: service._persist_vlm_transcriptions(
            [object()], analysis_run_id=other_id, expected_started_at=old
        ), AnalysisAttemptSuperseded)
        stale_db.rollback()
        must_block(lambda: service._persist_reviewing(
            other_id, candidate=object(), specs=[], actor_user_id=actor,
            expected_started_at=old
        ), AnalysisAttemptSuperseded)
        stale_db.rollback()
        assert service._fail_run(other_id, RuntimeError("old attempt"), actor_user_id=actor,
                                 expected_started_at=old) == "PROCESSING"
        assert snapshot_state(other_id) == expected

        # A current attempt still has permission to persist an empty synthetic
        # candidate and reach REVIEWING through the existing service method.
        candidate = SimpleNamespace(
            analysis=SimpleNamespace(overall_confidence=None),
            to_storage_dict=lambda: {"fixture": "current_attempt"},
        )
        assert service._persist_reviewing(
            other_id, candidate=candidate, specs=[], actor_user_id=actor,
            expected_started_at=newer,
        ) == "REVIEWING"
    assert snapshot_state(other_id)[0:3] == (
        "REVIEWING", newer, {"fixture": "current_attempt"}
    )
    print("V41D_STALE_ATTEMPT_VLM_CANDIDATE_FAILURE_FENCED_PASS", flush=True)

    # Exercise the real claim/failure path with no document or provider calls.
    queued_id = seed_run(actor, status="QUEUED")
    with patch.object(analysis_tasks, "get_object_storage", return_value=object()):
        result = analysis_tasks.run_profile_analysis.run(str(queued_id), str(actor))
    assert result["status"] == "FAILED"
    assert snapshot_state(queued_id)[0] == "FAILED"
    with SessionLocal() as db:
        assert not worker_guard_held(db, queued_id)
    print("V41D_CURRENT_CLAIM_AND_FAILURE_PATH_PASS", flush=True)

    # After the live worker releases ownership, actual inspection plus the
    # exclusive guard must allow legitimate administrator recovery.
    with SessionLocal() as db:
        recovered = recovery.recover_orphan(db, run_id, actor)
        assert recovered["status"] == "FAILED" and recovered["recovered"] is True
    assert snapshot_state(run_id)[0] == "FAILED"
    print("V41D_REAL_CELERY_RECOVERY_AFTER_RELEASE_PASS", flush=True)
    print("V41D_ISOLATED_EXECUTION_GUARD_PASS", flush=True)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--no-worker"]:
        check_absence_without_worker()
    else:
        assert not sys.argv[1:]
        main()
