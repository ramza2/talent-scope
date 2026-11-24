"""Disposable PostgreSQL V41 check. Never run against a shared database.

Requires the dedicated internal Docker network and fresh database created by
the V41-C verification shell. Celery inspection is mocked; real DB, FastAPI,
RBAC and CSRF checks are exercised. No real AI tasks or Redis calls.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

EXPECTED_URL = "postgresql+psycopg://v41check:v41-isolated-only@v41-db:5432/v41_isolated"
assert os.environ.get("V41_ISOLATED_TEST") == "1"
assert os.environ.get("DATABASE_URL") == EXPECTED_URL
assert os.environ.get("APP_ENV") == "test"

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.core.exceptions import AnalysisStateConflictError
from app.db.models.analysis import AnalysisRun, AnalysisDiffItem
from app.db.models.person import Person, PersonProfile
from app.db.models.revision import AuditLog
from app.db.models.user import AppUser
from app.db.session import SessionLocal
from app.main import create_app
from app.modules.analysis import orphan_recovery as recovery
from app.modules.analysis import orphan_router
from app.modules.auth.dependencies import AuthenticatedContext, get_current_user
from app.modules.auth.session_store import SessionRecord
from app.tasks import orphan_tasks

now = datetime.now(UTC)
old = now - timedelta(hours=1)
actor = uuid4()
target, active, recent, changed, audit_failure = [uuid4() for _ in range(5)]
person_ids = {run_id: uuid4() for run_id in (target, active, recent, changed, audit_failure)}
worker = "celery@v41-fixture"
absent = recovery.WorkerSnapshot(worker, (), True, "OK")
present = recovery.WorkerSnapshot(worker, (str(active),), True, "OK")

with SessionLocal() as db:
    assert db.execute(text("SELECT current_database(), current_user")).one() == (
        "v41_isolated", "v41check"
    )
    assert db.execute(select(func.count()).select_from(AnalysisRun)).scalar_one() == 0
    db.add(AppUser(id=actor, login_id="v41-synthetic-admin", password_hash="unused", name="Synthetic", role="ADMIN"))
    db.flush()
    for run_id, person_id in person_ids.items():
        db.add(Person(id=person_id, created_by=actor))
        db.flush()
        db.add(PersonProfile(person_id=person_id, name="Synthetic", profile_version=1))
        db.add(AnalysisRun(
            id=run_id, person_id=person_id, status="PROCESSING",
            started_at=now if run_id == recent else old,
            updated_at=now if run_id == recent else old,
            candidate_json={"synthetic": True}, base_profile_version=1,
        ))
    db.flush()
    db.add(AnalysisDiffItem(
        id=uuid4(), analysis_run_id=target, entity_type="PROFILE",
        change_type="NEW", review_status="PENDING",
    ))
    db.commit()

def read_state(run_id):
    with SessionLocal() as db:
        run = db.get(AnalysisRun, run_id)
        audits = db.execute(select(AuditLog).where(AuditLog.target_id == run_id)).scalars().all()
        return run.status, run.completed_at, run.error_message, run.candidate_json, [
            (a.user_id, a.action_type, a.after_json) for a in audits
        ]

initial = {run_id: read_state(run_id) for run_id in person_ids}
with patch.object(orphan_tasks, "inspect_worker", return_value=present):
    result = orphan_tasks.report_stale_analysis_runs.run()
assert result == {"stale": 4, "active": 1, "absent": 3, "unknown": 0, "worker_state": "OK"}, result
assert all(read_state(k) == v for k, v in initial.items())
print("V41C_MONITOR_READONLY_REAL_DB_PASS", flush=True)

app = create_app()
csrf = "v41-synthetic-csrf"
def context(role):
    return AuthenticatedContext(
        user=SimpleNamespace(id=actor, role=role),
        session_id="v41-synthetic-session",
        session=SessionRecord(user_id=actor, csrf_token=csrf, created_at=now.isoformat()),
    )

base = "/api/v1/analyses"
with TestClient(app) as client:
    assert client.get(f"{base}/{target}/orphan-check").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: context("USER")
    assert client.get(f"{base}/{target}/orphan-check").status_code == 403
    assert client.post(f"{base}/{target}/recover-orphan").status_code == 403
    app.dependency_overrides[get_current_user] = lambda: context("ADMIN")
    assert client.post(f"{base}/{target}/recover-orphan").status_code == 403
    client.cookies.set("ts_csrf", csrf)
    headers = {"X-CSRF-Token": csrf}
    with patch.object(recovery, "inspect_worker", return_value=present), patch.object(orphan_router, "inspect_worker", return_value=present):
        response = client.get(f"{base}/orphan-candidates")
        assert response.status_code == 200, response.status_code
        candidates = {item["analysis_id"]: item for item in response.json()["data"]}
        assert set(candidates) == {str(target), str(active), str(changed), str(audit_failure)}
        assert candidates[str(active)]["recovery_allowed"] is False
        assert candidates[str(target)]["recovery_allowed"] is True
        assert client.post(f"{base}/{active}/recover-orphan", headers=headers).status_code == 409
        assert client.post(f"{base}/{recent}/recover-orphan", headers=headers).status_code == 409
        response = client.get(f"{base}/{target}/orphan-check")
        assert response.status_code == 200 and response.json()["data"]["recovery_allowed"] is True
    assert all(read_state(k) == v for k, v in initial.items())
    print("V41C_API_RBAC_CSRF_AND_DENIAL_PASS", flush=True)
    with patch.object(recovery, "inspect_worker", return_value=absent):
        response = client.post(f"{base}/{target}/recover-orphan", headers=headers)
        assert response.status_code == 200, response.status_code
        assert response.json()["data"] == {"analysis_id": str(target), "status": "FAILED", "recovered": True}
        assert client.post(f"{base}/{target}/recover-orphan", headers=headers).status_code == 409

status, completed, reason, candidate, audits = read_state(target)
assert status == "FAILED" and completed is not None and reason == recovery.RECOVERY_ERROR
assert candidate == initial[target][3]
assert audits == [(actor, "ANALYSIS_FAILED", {"status": "FAILED", "reason": "orphaned_worker_task"})]
with SessionLocal() as db:
    assert db.get(PersonProfile, person_ids[target]).profile_version == 1
    assert db.execute(select(func.count()).select_from(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id == target)).scalar_one() == 1
assert all(read_state(k) == initial[k] for k in (active, recent, changed, audit_failure))
print("V41C_SINGLE_COMMIT_AUDIT_IDEMPOTENCY_PASS", flush=True)

def concurrent_completion():
    with SessionLocal() as other:
        run = other.get(AnalysisRun, changed)
        run.status = "REVIEWING"
        run.completed_at = datetime.now(UTC)
        other.commit()
    return absent

with SessionLocal() as db, patch.object(recovery, "inspect_worker", side_effect=concurrent_completion):
    try:
        recovery.recover_orphan(db, changed, actor)
    except AnalysisStateConflictError:
        pass
    else:
        raise AssertionError("late concurrent completion was overwritten")
assert read_state(changed)[0] == "REVIEWING" and read_state(changed)[4] == []
print("V41C_REAL_DB_CONCURRENT_STATUS_RECHECK_PASS", flush=True)

with SessionLocal() as db, patch.object(recovery, "inspect_worker", return_value=absent), patch.object(
    recovery.AnalysisRepository, "add_audit", side_effect=RuntimeError("synthetic audit failure")
):
    try:
        recovery.recover_orphan(db, audit_failure, actor)
    except RuntimeError as exc:
        assert str(exc) == "synthetic audit failure"
    else:
        raise AssertionError("expected audit failure")
assert read_state(audit_failure) == initial[audit_failure]
print("V41C_AUDIT_FAILURE_ROLLBACK_PASS", flush=True)
print("V41C_ISOLATED_POSTGRES_INTEGRATION_PASS", flush=True)
