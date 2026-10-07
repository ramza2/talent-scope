"""Deleted-person AnalysisRun list filtering + active-run cleanup."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

from tests.test_analysis import (  # noqa: E402
    _cleanup_person,
    _create_user,
    _queue_run,
    _seed_person_with_ready_doc,
)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _list_ids(db_session, *, status=None, person_id=None, page=1, page_size=20):
    from app.modules.analysis.repository import AnalysisRepository

    rows, total = AnalysisRepository(db_session).list_runs(
        status=status,
        person_id=person_id,
        sort="created_desc",
        page=page,
        page_size=page_size,
    )
    return [r.id for r in rows], total


def test_list_runs_includes_active_person_reviewing(db_session) -> None:
    admin = _create_user(
        db_session, login_id=f"la_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    run = _queue_run(db_session, person.id, document.id)
    run.status = "REVIEWING"
    db_session.commit()

    ids, total = _list_ids(db_session)
    assert run.id in ids
    assert total >= 1
    _cleanup_person(db_session, person.id, admin.id)


def test_list_runs_excludes_deleted_person_history(db_session) -> None:
    from app.db.models.person import Person

    admin = _create_user(
        db_session, login_id=f"ld_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    reviewing = _queue_run(db_session, person.id, document.id)
    reviewing.status = "REVIEWING"
    failed = _queue_run(db_session, person.id, document.id)
    failed.status = "FAILED"
    db_session.commit()

    person_row = db_session.get(Person, person.id)
    assert person_row is not None
    person_row.status = "DELETED"
    person_row.deleted_at = datetime.now(UTC)
    db_session.commit()

    ids, _total = _list_ids(db_session)
    assert reviewing.id not in ids
    assert failed.id not in ids

    reviewing_ids, reviewing_total = _list_ids(db_session, status="REVIEWING")
    assert reviewing.id not in reviewing_ids
    assert reviewing_total == len(reviewing_ids)

    _cleanup_person(db_session, person.id, admin.id)


def test_list_runs_total_excludes_deleted_person(db_session) -> None:
    from app.db.models.person import Person

    admin = _create_user(
        db_session, login_id=f"lt_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    active_person, active_doc = _seed_person_with_ready_doc(db_session, admin.id)
    deleted_person, deleted_doc = _seed_person_with_ready_doc(db_session, admin.id)
    active_run = _queue_run(db_session, active_person.id, active_doc.id)
    active_run.status = "REVIEWING"
    deleted_run = _queue_run(db_session, deleted_person.id, deleted_doc.id)
    deleted_run.status = "REVIEWING"
    db_session.commit()

    before_ids, before_total = _list_ids(db_session, status="REVIEWING")
    assert active_run.id in before_ids
    assert deleted_run.id in before_ids

    person_row = db_session.get(Person, deleted_person.id)
    assert person_row is not None
    person_row.status = "DELETED"
    person_row.deleted_at = datetime.now(UTC)
    db_session.commit()

    after_ids, after_total = _list_ids(db_session, status="REVIEWING")
    assert active_run.id in after_ids
    assert deleted_run.id not in after_ids
    assert after_total == before_total - 1
    assert after_total == len(after_ids)

    _cleanup_person(db_session, active_person.id, None)
    _cleanup_person(db_session, deleted_person.id, admin.id)


def test_person_delete_cancels_only_active_runs(db_session) -> None:
    from app.db.models.revision import AuditLog
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService

    admin = _create_user(
        db_session, login_id=f"pc_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    deferred = _queue_run(db_session, person.id, document.id)
    deferred.status = "DEFERRED"
    queued = _queue_run(db_session, person.id, document.id)
    processing = _queue_run(db_session, person.id, document.id)
    processing.status = "PROCESSING"
    reviewing = _queue_run(db_session, person.id, document.id)
    reviewing.status = "REVIEWING"
    failed = _queue_run(db_session, person.id, document.id)
    failed.status = "FAILED"
    confirmed = _queue_run(db_session, person.id, document.id)
    confirmed.status = "CONFIRMED"
    db_session.commit()

    PeopleService(db_session).update_status(
        person.id,
        PersonStatusUpdateRequest(status="DELETED"),
        admin.id,
    )

    db_session.refresh(deferred)
    db_session.refresh(queued)
    db_session.refresh(processing)
    db_session.refresh(reviewing)
    db_session.refresh(failed)
    db_session.refresh(confirmed)
    assert deferred.status == "CANCELLED"
    assert queued.status == "CANCELLED"
    assert processing.status == "CANCELLED"
    assert reviewing.status == "REVIEWING"
    assert failed.status == "FAILED"
    assert confirmed.status == "CONFIRMED"

    audits = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action_type == "ANALYSIS_CANCEL",
                AuditLog.target_id.in_([deferred.id, queued.id, processing.id]),
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 3
    for audit in audits:
        meta = audit.metadata_json or {}
        assert meta.get("reason") == "PERSON_DELETED"
        assert meta.get("person_id") == str(person.id)

    _cleanup_person(db_session, person.id, admin.id)


def test_persist_reviewing_keeps_cancelled_after_person_delete(db_session) -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.service import AnalysisService
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService
    from app.storage.s3 import get_object_storage

    admin = _create_user(
        db_session, login_id=f"pr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    run = _queue_run(db_session, person.id, document.id)
    run.status = "PROCESSING"
    db_session.commit()

    PeopleService(db_session).update_status(
        person.id,
        PersonStatusUpdateRequest(status="DELETED"),
        admin.id,
    )
    db_session.refresh(run)
    assert run.status == "CANCELLED"

    service = AnalysisService(db_session, storage=get_object_storage())
    candidate = ProfileCandidateDocument.model_validate(
        {
            "schema_version": "profile-candidate-v1",
            "profile": {"name": "분석대상"},
            "analysis": {"overall_confidence": 0.5},
        }
    )
    status = service._persist_reviewing(
        run.id,
        candidate=candidate,
        specs=[],
        actor_user_id=admin.id,
    )
    assert status == "CANCELLED"
    db_session.refresh(run)
    assert run.status == "CANCELLED"
    _cleanup_person(db_session, person.id, admin.id)


def test_restore_person_resurfaces_historical_runs(db_session) -> None:
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService

    admin = _create_user(
        db_session, login_id=f"rs_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    reviewing = _queue_run(db_session, person.id, document.id)
    reviewing.status = "REVIEWING"
    failed = _queue_run(db_session, person.id, document.id)
    failed.status = "FAILED"
    db_session.commit()

    people = PeopleService(db_session)
    people.update_status(
        person.id,
        PersonStatusUpdateRequest(status="DELETED"),
        admin.id,
    )
    ids_deleted, _ = _list_ids(db_session)
    assert reviewing.id not in ids_deleted
    assert failed.id not in ids_deleted

    people.update_status(
        person.id,
        PersonStatusUpdateRequest(status="ACTIVE"),
        admin.id,
    )
    ids_restored, _ = _list_ids(db_session)
    assert reviewing.id in ids_restored
    assert failed.id in ids_restored
    db_session.refresh(reviewing)
    db_session.refresh(failed)
    assert reviewing.status == "REVIEWING"
    assert failed.status == "FAILED"
    _cleanup_person(db_session, person.id, admin.id)
