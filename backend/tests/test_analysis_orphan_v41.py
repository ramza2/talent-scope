"""V41 safety gates; no DB, Redis, GPU or Celery worker needed."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

from app.core.exceptions import AnalysisStateConflictError  # noqa: E402
from app.modules.analysis import orphan_recovery as v41  # noqa: E402


def fake_worker(*, active=None, reserved=None, scheduled=None, worker="celery@worker"):
    calls = {
        "ping": {worker: {"ok": "pong"}},
        "active": {worker: active if active is not None else []},
        "reserved": {worker: reserved if reserved is not None else []},
        "scheduled": {worker: scheduled if scheduled is not None else []},
    }
    return SimpleNamespace(
        control=SimpleNamespace(
            inspect=lambda timeout: SimpleNamespace(
                **{name: (lambda n=name: calls[n]) for name in calls}
            )
        )
    )


def fake_run(*, age_minutes=50, status="PROCESSING", updated_minutes=None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(), person_id=uuid4(), status=status,
        started_at=now - timedelta(minutes=age_minutes),
        updated_at=now - timedelta(minutes=updated_minutes or age_minutes),
        completed_at=None,
        error_message=None,
    )


def test_single_worker_empty_lists_is_only_absent_case():
    run = fake_run()
    snapshot = v41.inspect_worker(app=fake_worker())
    assert snapshot.healthy is True
    assert snapshot.target_state(run.id) == "ABSENT"


def test_task_seen_in_any_celery_queue_blocks_recovery():
    run = fake_run()
    task = {"name": "app.tasks.analysis_tasks.run_profile_analysis", "args": [str(run.id)]}
    for kind in ("active", "reserved", "scheduled"):
        snapshot = v41.inspect_worker(app=fake_worker(**{kind: [task]}))
        assert snapshot.target_state(run.id) == "ACTIVE"


def test_unavailable_worker_is_never_absent():
    app = fake_worker()
    app.control.inspect = lambda timeout: SimpleNamespace(
        ping=lambda: {}, active=lambda: {}, reserved=lambda: {}, scheduled=lambda: {}
    )
    snapshot = v41.inspect_worker(app=app)
    assert snapshot.healthy is False
    assert snapshot.target_state(uuid4()) == "UNKNOWN"


def test_malformed_celery_payload_fails_closed():
    snapshot = v41.inspect_worker(app=fake_worker(active=[{"id": "abc"}]))
    assert snapshot.target_state(uuid4()) == "UNKNOWN"


def test_multiple_workers_fails_closed():
    app = fake_worker()
    app.control.inspect = lambda timeout: SimpleNamespace(
        ping=lambda: {"celery@worker1": {"ok": "pong"}, "celery@worker2": {"ok": "pong"}},
        active=lambda: {}, reserved=lambda: {}, scheduled=lambda: {},
    )
    assert v41.inspect_worker(app=app).target_state(uuid4()) == "UNKNOWN"


def test_recent_or_modified_run_is_not_stale():
    assert not v41._is_stale(fake_run(age_minutes=10), datetime.now(UTC))
    assert not v41._is_stale(fake_run(age_minutes=60, updated_minutes=2), datetime.now(UTC))
    assert not v41._is_stale(fake_run(status="REVIEWING"), datetime.now(UTC))
    assert v41._is_stale(fake_run(age_minutes=50), datetime.now(UTC))


def test_recovery_does_not_touch_db_if_worker_is_active(monkeypatch):
    run = fake_run()
    db = MagicMock()
    db.get.return_value = run
    monkeypatch.setattr(
        v41, "inspect_worker",
        lambda: v41.WorkerSnapshot("celery@worker", (str(run.id),), True, "OK"),
    )
    with pytest.raises(AnalysisStateConflictError):
        v41.recover_orphan(db, run.id, uuid4())
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_recovery_rechecks_worker_while_run_row_locked(monkeypatch):
    run = fake_run()
    db = MagicMock()
    db.get.return_value = run
    repo = MagicMock()
    repo.get_run.return_value = run
    monkeypatch.setattr(v41, "AnalysisRepository", lambda _db: repo)
    snapshots = iter([
        v41.WorkerSnapshot("celery@worker", (), True, "OK"),
        v41.WorkerSnapshot("celery@worker", (str(run.id),), True, "OK"),
    ])
    monkeypatch.setattr(v41, "inspect_worker", lambda: next(snapshots))
    with pytest.raises(AnalysisStateConflictError):
        v41.recover_orphan(db, run.id, uuid4())
    repo.mark_failed.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


def test_recovery_rechecks_db_after_lock(monkeypatch):
    run = fake_run()
    db = MagicMock()
    db.get.return_value = run
    repo = MagicMock()

    def mutate_and_return(*_a, **_kw):
        run.status = "FAILED"
        return run

    repo.get_run.side_effect = mutate_and_return
    monkeypatch.setattr(v41, "AnalysisRepository", lambda _db: repo)
    monkeypatch.setattr(v41, "inspect_worker", lambda: v41.WorkerSnapshot("celery@worker", (), True, "OK"))
    with pytest.raises(AnalysisStateConflictError):
        v41.recover_orphan(db, run.id, uuid4())
    repo.mark_failed.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


def test_recovery_commits_one_run_and_audit(monkeypatch):
    run = fake_run()
    db = MagicMock()
    db.get.return_value = run
    repo = MagicMock()
    repo.get_run.return_value = run
    monkeypatch.setattr(v41, "AnalysisRepository", lambda _db: repo)
    monkeypatch.setattr(v41, "inspect_worker", lambda: v41.WorkerSnapshot("celery@worker", (), True, "OK"))
    actor = uuid4()
    result = v41.recover_orphan(db, run.id, actor)
    assert result == {"analysis_id": run.id, "status": "FAILED", "recovered": True}
    db.expire.assert_called_once_with(run)
    repo.mark_failed.assert_called_once_with(run, v41.RECOVERY_ERROR)
    kwargs = repo.add_audit.call_args.kwargs
    assert kwargs["actor_user_id"] == actor
    assert kwargs["target_id"] == run.id
    assert kwargs["before"]["status"] == "PROCESSING"
    assert kwargs["after"]["status"] == "FAILED"
    db.commit.assert_called_once()


def test_orphan_routes_bootstrap_and_respect_auth_contract():
    """Catch module-vs-APIRouter import mistakes before deployment."""
    from fastapi.routing import APIRoute

    from app.main import create_app

    routes = [route for route in create_app().routes if isinstance(route, APIRoute)]
    expected = {
        ("GET", "/api/v1/analyses/orphan-candidates"),
        ("GET", "/api/v1/analyses/{analysis_id}/orphan-check"),
        ("POST", "/api/v1/analyses/{analysis_id}/recover-orphan"),
    }
    found = {(method, route.path) for route in routes for method in route.methods}
    assert expected <= found

    for method, path in expected:
        matches = [route for route in routes if route.path == path and method in route.methods]
        assert len(matches) == 1
        dependencies = {item.call.__name__ for item in matches[0].dependant.dependencies}
        assert "require_admin" in dependencies
        if method == "POST":
            assert "require_csrf" in dependencies

    paths = [route.path for route in routes]
    assert paths.index("/api/v1/analyses/orphan-candidates") < paths.index(
        "/api/v1/analyses/{analysis_id}"
    )
