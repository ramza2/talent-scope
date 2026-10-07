"""Upload-batch auto analysis + active-run create guard."""

from __future__ import annotations

import inspect
import os
import uuid
from collections.abc import Generator

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
    _ensure_doc_type,
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


def _seed_second_ready_doc(db_session, person, user_id, *, status: str = "READY"):
    from app.db.models.document import Document, DocumentGroup, DocumentPage

    doc_type = _ensure_doc_type(db_session)
    group = DocumentGroup(
        person_id=person.id,
        document_type_code=doc_type,
        title="경력기술서",
    )
    db_session.add(group)
    db_session.flush()
    document = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="career.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256=(uuid.uuid4().hex + uuid.uuid4().hex),
        processing_status=status,
        uploaded_by=user_id,
    )
    db_session.add(document)
    db_session.flush()
    if status == "READY":
        db_session.add(
            DocumentPage(
                document_id=document.id,
                page_no=1,
                extracted_text="경력 Python FastAPI",
                layout_json={"needs_vlm": False},
                extraction_method="TEXT_PARSER",
            )
        )
    db_session.commit()
    db_session.refresh(document)
    return document


def _count_person_runs(db_session, person_id):
    from app.db.models.analysis import AnalysisRun

    return list(
        db_session.execute(
            select(AnalysisRun).where(AnalysisRun.person_id == person_id)
        )
        .scalars()
        .all()
    )


def test_manual_create_blocked_when_queued(db_session, monkeypatch: pytest.MonkeyPatch):
    from app.core.exceptions import AnalysisStateConflictError
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"aq_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    first = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    assert first.status == "QUEUED"
    with pytest.raises(AnalysisStateConflictError):
        service.create_analysis(
            CreateAnalysisRequest(
                person_id=person.id,
                document_ids=[document.id],
                analysis_type="PROFILE",
            ),
            admin.id,
        )
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_manual_create_blocked_when_processing(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.core.exceptions import AnalysisStateConflictError
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"ap_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    created = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    run = db_session.get(AnalysisRun, created.analysis_id)
    assert run is not None
    run.status = "PROCESSING"
    db_session.commit()

    with pytest.raises(AnalysisStateConflictError):
        service.create_analysis(
            CreateAnalysisRequest(
                person_id=person.id,
                document_ids=[document.id],
                analysis_type="PROFILE",
            ),
            admin.id,
        )
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


@pytest.mark.parametrize("terminal_status", ["REVIEWING", "FAILED"])
def test_manual_create_allowed_after_terminal_status(
    db_session, monkeypatch: pytest.MonkeyPatch, terminal_status: str
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"at_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    first = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    run = db_session.get(AnalysisRun, first.analysis_id)
    assert run is not None
    run.status = terminal_status
    db_session.commit()

    second = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    assert second.status == "QUEUED"
    assert second.analysis_id != first.analysis_id
    assert len(_count_person_runs(db_session, person.id)) == 2
    _cleanup_person(db_session, person.id, admin.id)


def test_two_document_auto_batch_waits_then_creates_once(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRunDocument
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"ab_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc1 = _seed_person_with_ready_doc(db_session, admin.id)
    doc2 = _seed_second_ready_doc(
        db_session, person, admin.id, status="PROCESSING"
    )
    service = AnalysisService(db_session, storage=get_object_storage())
    batch = [doc1.id, doc2.id]
    new_ids = [doc1.id, doc2.id]

    assert (
        service.create_analysis_for_ready_documents(
            batch, new_document_ids=new_ids
        )
        is None
    )
    assert _count_person_runs(db_session, person.id) == []

    doc2.processing_status = "READY"
    db_session.commit()

    first = service.create_analysis_for_ready_documents(
        batch, new_document_ids=new_ids
    )
    assert first is not None
    linked = list(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == first.analysis_id
            )
        )
        .scalars()
        .all()
    )
    assert set(linked) == set(batch)
    assert len(linked) == 2

    second = service.create_analysis_for_ready_documents(
        batch, new_document_ids=new_ids
    )
    assert second is None
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_active_guard_evaluated_after_person_for_update_contract() -> None:
    from app.modules.analysis import service as analysis_service_mod

    source = inspect.getsource(analysis_service_mod.AnalysisService.create_analysis)
    person_idx = source.find("get_person(payload.person_id, for_update=True)")
    active_idx = source.find("get_manual_create_blocking_run(payload.person_id)")
    assert person_idx != -1
    assert active_idx != -1
    assert person_idx < active_idx


def test_auto_batch_historical_check_after_person_for_update_contract() -> None:
    from app.modules.analysis import service as analysis_service_mod

    source = inspect.getsource(
        analysis_service_mod.AnalysisService.create_analysis_for_ready_documents
    )
    person_idx = source.find("get_person(person_id, for_update=True)")
    hist_idx = source.find("has_run_covering_document_batch(person_id, batch)")
    assert person_idx != -1
    assert hist_idx != -1
    assert person_idx < hist_idx


def test_mixed_new_reused_batch_includes_both(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRunDocument
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"mx_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, reused = _seed_person_with_ready_doc(db_session, admin.id)
    new_doc = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())

    result = service.create_analysis_for_ready_documents(
        [reused.id, new_doc.id],
        new_document_ids=[new_doc.id],
    )
    assert result is not None
    linked = set(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == result.analysis_id
            )
        )
        .scalars()
        .all()
    )
    assert linked == {reused.id, new_doc.id}
    _cleanup_person(db_session, person.id, admin.id)


def test_all_reused_batch_does_not_force_auto_run(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"ar_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    assert (
        service.create_analysis_for_ready_documents(
            [doc.id],
            new_document_ids=[],
        )
        is None
    )
    assert _count_person_runs(db_session, person.id) == []
    _cleanup_person(db_session, person.id, admin.id)


def test_single_document_auto_analysis_still_creates_one(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRunDocument
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"sd_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    first = service.create_analysis_for_ready_document(document.id)
    assert first is not None
    linked = list(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == first.analysis_id
            )
        )
        .scalars()
        .all()
    )
    assert linked == [document.id]
    assert (
        service.create_analysis_for_ready_document(document.id) is None
    )
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_resolve_enqueue_passes_batch_ids(monkeypatch: pytest.MonkeyPatch):
    from app.modules.documents.service import DocumentService

    calls: list[tuple] = []

    def _capture(doc_id, *, batch_document_ids=None, new_document_ids=None):
        calls.append((doc_id, list(batch_document_ids or []), list(new_document_ids or [])))

    monkeypatch.setattr(
        "app.tasks.document_tasks.enqueue_document_processing", _capture
    )
    # Avoid constructing a real DocumentService DB session: call helper unbound.
    svc = object.__new__(DocumentService)
    d1, d2, d3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    DocumentService._enqueue_processing(
        svc,
        [d1, d2],
        batch_document_ids=[d1, d2, d3],
        new_document_ids=[d1, d2],
    )
    assert len(calls) == 2
    assert calls[0][0] == d1
    assert calls[0][1] == [d1, d2, d3]
    assert calls[0][2] == [d1, d2]
    assert calls[1][0] == d2


def test_active_processing_creates_deferred_run_with_documents(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun, AnalysisRunDocument
    from app.modules.analysis.repository import AnalysisRepository
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    defer_calls: list[dict] = []

    def _capture_defer(analysis_run_id, *, attempt=1):
        defer_calls.append({"analysis_run_id": analysis_run_id, "attempt": attempt})

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        _capture_defer,
    )

    admin = _create_user(
        db_session, login_id=f"df_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    batch = [doc_a.id, doc_b.id]
    result = service.create_analysis_for_ready_documents(
        batch, new_document_ids=batch
    )
    assert result is not None
    assert result.status == "DEFERRED"
    assert len(defer_calls) == 1
    assert defer_calls[0]["analysis_run_id"] == result.analysis_id
    assert defer_calls[0]["attempt"] == 1

    deferred = db_session.get(AnalysisRun, result.analysis_id)
    assert deferred is not None
    assert deferred.status == "DEFERRED"
    linked = set(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == result.analysis_id
            )
        )
        .scalars()
        .all()
    )
    assert linked == set(batch)

    # List API / repository can filter DEFERRED.
    rows, total = AnalysisRepository(db_session).list_runs(
        status="DEFERRED",
        person_id=person.id,
        sort="created_desc",
        page=1,
        page_size=20,
    )
    assert total == 1
    assert rows[0].id == result.analysis_id
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_initial_enqueue_failure_marks_failed(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun, AnalysisRunDocument
    from app.db.models.revision import AuditLog
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import document_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )

    def _enqueue_boom(*_a, **_k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        _enqueue_boom,
    )

    admin = _create_user(
        db_session, login_id=f"eq_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    batch = [doc_a.id, doc_b.id]
    result = service.create_analysis_for_ready_documents(
        batch, new_document_ids=batch
    )
    assert result is not None
    assert result.status == "FAILED"

    failed = db_session.get(AnalysisRun, result.analysis_id)
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.error_message == "AI queue unavailable"

    linked = set(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == result.analysis_id
            )
        )
        .scalars()
        .all()
    )
    assert linked == set(batch)

    audit = db_session.execute(
        select(AuditLog).where(
            AuditLog.action_type == "ANALYSIS_ENQUEUE_FAILED",
            AuditLog.target_id == result.analysis_id,
        )
    ).scalar_one_or_none()
    assert audit is not None
    meta = audit.metadata_json or {}
    assert meta.get("reason") == "DEFERRED_QUEUE_UNAVAILABLE"
    assert meta.get("person_id") == str(person.id)

    # Document processing caller must not see the broker error.
    document_tasks._maybe_start_auto_profile_analysis(
        db_session,
        doc_a.id,
        batch_document_ids=batch,
        new_document_ids=batch,
    )
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_initial_enqueue_failure_skips_non_deferred_status(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )

    def _enqueue_flip_and_boom(analysis_run_id, *, attempt=1):
        run = db_session.get(AnalysisRun, analysis_run_id)
        assert run is not None
        run.status = "REVIEWING"
        db_session.commit()
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        _enqueue_flip_and_boom,
    )

    admin = _create_user(
        db_session, login_id=f"es_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    result = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert result is not None
    assert result.status == "DEFERRED"

    run = db_session.get(AnalysisRun, result.analysis_id)
    assert run is not None
    assert run.status == "REVIEWING"
    assert run.error_message is None
    _cleanup_person(db_session, person.id, admin.id)


def test_duplicate_ready_callback_creates_single_deferred(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import document_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"dup_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    run = db_session.get(AnalysisRun, active.analysis_id)
    assert run is not None
    run.status = "PROCESSING"
    db_session.commit()

    batch = [doc_a.id, doc_b.id]
    document_tasks._maybe_start_auto_profile_analysis(
        db_session,
        doc_a.id,
        batch_document_ids=batch,
        new_document_ids=batch,
    )
    document_tasks._maybe_start_auto_profile_analysis(
        db_session,
        doc_b.id,
        batch_document_ids=batch,
        new_document_ids=batch,
    )
    runs = _count_person_runs(db_session, person.id)
    deferred = [r for r in runs if r.status == "DEFERRED"]
    assert len(deferred) == 1
    assert len(runs) == 2  # PROCESSING + DEFERRED
    _cleanup_person(db_session, person.id, admin.id)


def test_manual_create_blocked_when_deferred(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.core.exceptions import AnalysisStateConflictError
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"md_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    deferred = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert deferred is not None
    assert deferred.status == "DEFERRED"

    with pytest.raises(AnalysisStateConflictError):
        service.create_analysis(
            CreateAnalysisRequest(
                person_id=person.id,
                document_ids=[doc_a.id],
                analysis_type="PROFILE",
            ),
            admin.id,
        )
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_retry_reschedules_while_active(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    scheduled: list[dict] = []

    def _capture_apply_async(*_a, **kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(
        analysis_tasks.retry_deferred_auto_profile_analysis,
        "apply_async",
        _capture_apply_async,
    )

    admin = _create_user(
        db_session, login_id=f"dr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    created = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert created is not None
    assert created.status == "DEFERRED"
    # Initial DEFERRED create schedules attempt=1; clear before retry task.
    scheduled.clear()

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(created.analysis_id),
        attempt=1,
    )
    assert result["status"] == "DEFERRED"
    assert len(scheduled) == 1
    assert scheduled[0]["countdown"] == analysis_tasks.AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS
    assert scheduled[0]["kwargs"]["attempt"] == 2
    assert scheduled[0]["kwargs"]["analysis_run_id"] == str(created.analysis_id)
    deferred = db_session.get(AnalysisRun, created.analysis_id)
    assert deferred is not None
    assert deferred.status == "DEFERRED"
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_retry_promotes_same_run_after_active_clears(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    enqueued: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis",
        lambda run_id, *_a, **_k: enqueued.append(run_id),
    )
    monkeypatch.setattr(
        analysis_tasks.retry_deferred_auto_profile_analysis,
        "apply_async",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"dc_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    batch = [doc_a.id, doc_b.id]
    deferred = service.create_analysis_for_ready_documents(
        batch, new_document_ids=batch
    )
    assert deferred is not None
    assert deferred.status == "DEFERRED"
    deferred_id = deferred.analysis_id
    enqueued.clear()

    run = db_session.get(AnalysisRun, active.analysis_id)
    assert run is not None
    run.status = "REVIEWING"
    db_session.commit()

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(deferred_id),
        attempt=3,
    )
    assert result["status"] == "QUEUED"
    assert result["analysis_run_id"] == str(deferred_id)
    promoted = db_session.get(AnalysisRun, deferred_id)
    assert promoted is not None
    assert promoted.status == "QUEUED"
    assert enqueued == [deferred_id]
    assert len(_count_person_runs(db_session, person.id)) == 2
    _cleanup_person(db_session, person.id, admin.id)


def test_two_deferred_concurrent_promote_only_one_queued(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    reschedule: list[dict] = []
    monkeypatch.setattr(
        analysis_tasks.retry_deferred_auto_profile_analysis,
        "apply_async",
        lambda *_a, **kwargs: reschedule.append(kwargs),
    )

    admin = _create_user(
        db_session, login_id=f"td_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    d1 = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    d2 = service.create_analysis_for_ready_documents(
        [doc_b.id], new_document_ids=[doc_b.id]
    )
    assert d1 is not None and d1.status == "DEFERRED"
    assert d2 is not None and d2.status == "DEFERRED"
    reschedule.clear()

    run = db_session.get(AnalysisRun, active.analysis_id)
    assert run is not None
    run.status = "REVIEWING"
    db_session.commit()

    first = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(d1.analysis_id), attempt=1
    )
    second = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(d2.analysis_id), attempt=1
    )
    assert first["status"] == "QUEUED"
    assert second["status"] == "DEFERRED"
    r1 = db_session.get(AnalysisRun, d1.analysis_id)
    r2 = db_session.get(AnalysisRun, d2.analysis_id)
    assert r1 is not None and r1.status == "QUEUED"
    assert r2 is not None and r2.status == "DEFERRED"
    assert len(reschedule) == 1
    assert reschedule[0]["kwargs"]["analysis_run_id"] == str(d2.analysis_id)
    queued = [r for r in _count_person_runs(db_session, person.id) if r.status == "QUEUED"]
    assert len(queued) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_defer_exhaustion_marks_failed(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        analysis_tasks.retry_deferred_auto_profile_analysis,
        "apply_async",
        lambda *_a, **kwargs: scheduled.append(kwargs),
    )

    admin = _create_user(
        db_session, login_id=f"mxa_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    deferred = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert deferred is not None

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(deferred.analysis_id),
        attempt=analysis_tasks.AUTO_ANALYSIS_DEFER_MAX_ATTEMPTS,
    )
    assert result["status"] == "FAILED"
    assert scheduled == []
    failed = db_session.get(AnalysisRun, deferred.analysis_id)
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.error_message is not None
    assert "재시도 한도" in failed.error_message

    # Existing Retry path still works after exhaustion.
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "REVIEWING"
    db_session.commit()
    retried = service.retry_analysis(deferred.analysis_id, admin.id)
    assert retried.status == "QUEUED"
    assert retried.analysis_id == deferred.analysis_id
    _cleanup_person(db_session, person.id, admin.id)


def test_person_delete_cancels_deferred_and_retry_skips(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        analysis_tasks.retry_deferred_auto_profile_analysis,
        "apply_async",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"pdd_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    deferred = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert deferred is not None
    deferred_id = deferred.analysis_id

    PeopleService(db_session).update_status(
        person.id,
        PersonStatusUpdateRequest(status="DELETED"),
        admin.id,
    )
    cancelled = db_session.get(AnalysisRun, deferred_id)
    assert cancelled is not None
    assert cancelled.status == "CANCELLED"
    active_after = db_session.get(AnalysisRun, active.analysis_id)
    assert active_after is not None
    assert active_after.status == "CANCELLED"

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(deferred_id),
        attempt=2,
    )
    assert result["status"] == "CANCELLED"
    db_session.refresh(cancelled)
    assert cancelled.status == "CANCELLED"
    _cleanup_person(db_session, person.id, admin.id)


def test_same_batch_failed_blocks_auto_but_manual_retry_works(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"ff_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    created = service.create_analysis_for_ready_document(doc.id)
    assert created is not None
    run = db_session.get(AnalysisRun, created.analysis_id)
    assert run is not None
    run.status = "FAILED"
    db_session.commit()

    assert service.create_analysis_for_ready_document(doc.id) is None
    assert len(_count_person_runs(db_session, person.id)) == 1

    retried = service.retry_analysis(created.analysis_id, admin.id)
    assert retried.status == "QUEUED"
    assert retried.analysis_id == created.analysis_id
    db_session.refresh(run)
    assert run.status == "QUEUED"
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_reschedule_enqueue_failure_marks_failed(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.db.models.revision import AuditLog
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"rsf_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    deferred = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert deferred is not None
    assert deferred.status == "DEFERRED"

    def _reschedule_boom(*_a, **_k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        _reschedule_boom,
    )

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(deferred.analysis_id),
        attempt=1,
    )
    assert result["status"] == "FAILED"
    failed = db_session.get(AnalysisRun, deferred.analysis_id)
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.error_message == "AI queue unavailable"

    audit = db_session.execute(
        select(AuditLog).where(
            AuditLog.action_type == "ANALYSIS_ENQUEUE_FAILED",
            AuditLog.target_id == deferred.analysis_id,
        )
    ).scalar_one_or_none()
    assert audit is not None
    meta = audit.metadata_json or {}
    assert meta.get("reason") == "DEFERRED_RESCHEDULE_UNAVAILABLE"
    assert meta.get("attempt") == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_reschedule_enqueue_failure_skips_non_deferred(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"rss_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    deferred = service.create_analysis_for_ready_documents(
        [doc_a.id], new_document_ids=[doc_a.id]
    )
    assert deferred is not None

    def _flip_and_boom(analysis_run_id, *, attempt=1):
        run = db_session.get(AnalysisRun, analysis_run_id)
        assert run is not None
        run.status = "CANCELLED"
        db_session.commit()
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        _flip_and_boom,
    )

    result = analysis_tasks.retry_deferred_auto_profile_analysis.run(
        analysis_run_id=str(deferred.analysis_id),
        attempt=1,
    )
    assert result["status"] == "CANCELLED"
    run = db_session.get(AnalysisRun, deferred.analysis_id)
    assert run is not None
    assert run.status == "CANCELLED"
    assert run.error_message is None or "AI queue unavailable" not in (
        run.error_message or ""
    )
    _cleanup_person(db_session, person.id, admin.id)


@pytest.mark.parametrize("blocker_status", ["PROCESSING", "QUEUED", "DEFERRED"])
def test_failed_retry_blocked_by_active_or_deferred(
    db_session, monkeypatch: pytest.MonkeyPatch, blocker_status: str
):
    from app.core.exceptions import AnalysisStateConflictError
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"rb_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc_failed = _seed_person_with_ready_doc(db_session, admin.id)
    doc_blocker = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())

    failed = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[doc_failed.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    failed_run = db_session.get(AnalysisRun, failed.analysis_id)
    assert failed_run is not None
    failed_run.status = "FAILED"
    db_session.commit()

    if blocker_status == "DEFERRED":
        # Need an executable active first to create DEFERRED, then clear it.
        active = service.create_analysis(
            CreateAnalysisRequest(
                person_id=person.id,
                document_ids=[doc_blocker.id],
                analysis_type="PROFILE",
            ),
            admin.id,
        )
        active_run = db_session.get(AnalysisRun, active.analysis_id)
        assert active_run is not None
        active_run.status = "PROCESSING"
        db_session.commit()
        doc_extra = _seed_second_ready_doc(
            db_session, person, admin.id, status="READY"
        )
        deferred = service.create_analysis_for_ready_documents(
            [doc_extra.id], new_document_ids=[doc_extra.id]
        )
        assert deferred is not None and deferred.status == "DEFERRED"
        active_run.status = "REVIEWING"
        db_session.commit()
    else:
        blocker = service.create_analysis(
            CreateAnalysisRequest(
                person_id=person.id,
                document_ids=[doc_blocker.id],
                analysis_type="PROFILE",
            ),
            admin.id,
        )
        blocker_run = db_session.get(AnalysisRun, blocker.analysis_id)
        assert blocker_run is not None
        blocker_run.status = blocker_status
        db_session.commit()

    with pytest.raises(AnalysisStateConflictError):
        service.retry_analysis(failed.analysis_id, admin.id)
    db_session.refresh(failed_run)
    assert failed_run.status == "FAILED"
    _cleanup_person(db_session, person.id, admin.id)


def test_failed_retry_succeeds_without_blocker(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"rok_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    created = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    run = db_session.get(AnalysisRun, created.analysis_id)
    assert run is not None
    run.status = "FAILED"
    db_session.commit()

    retried = service.retry_analysis(created.analysis_id, admin.id)
    assert retried.status == "QUEUED"
    assert retried.analysis_id == created.analysis_id
    db_session.refresh(run)
    assert run.status == "QUEUED"
    _cleanup_person(db_session, person.id, admin.id)


def test_failed_retry_forbidden_for_deleted_person(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.core.exceptions import NotFoundError
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService
    from app.storage.s3 import get_object_storage

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"rdp_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    service = AnalysisService(db_session, storage=get_object_storage())
    created = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[document.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    run = db_session.get(AnalysisRun, created.analysis_id)
    assert run is not None
    run.status = "FAILED"
    db_session.commit()

    PeopleService(db_session).update_status(
        person.id,
        PersonStatusUpdateRequest(status="DELETED"),
        admin.id,
    )

    with pytest.raises(NotFoundError):
        service.retry_analysis(created.analysis_id, admin.id)
    db_session.refresh(run)
    assert run.status == "FAILED"
    _cleanup_person(db_session, person.id, admin.id)


def test_old_batch_retry_task_registered_and_creates_deferred(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks
    from app.tasks.celery_app import celery_app

    old_name = "app.tasks.analysis_tasks.retry_auto_profile_analysis_batch"
    assert old_name in celery_app.tasks
    assert (
        "app.tasks.analysis_tasks.retry_deferred_auto_profile_analysis"
        in celery_app.tasks
    )
    assert (
        celery_app.tasks[old_name]
        is not celery_app.tasks[
            "app.tasks.analysis_tasks.retry_deferred_auto_profile_analysis"
        ]
    )

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_deferred_auto_profile_analysis",
        lambda *_a, **_k: None,
    )

    admin = _create_user(
        db_session, login_id=f"old_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    active = service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    active_run = db_session.get(AnalysisRun, active.analysis_id)
    assert active_run is not None
    active_run.status = "PROCESSING"
    db_session.commit()

    result = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(doc_a.id)],
        new_document_ids=[str(doc_a.id)],
        attempt=2,
    )
    assert result["status"] == "DEFERRED"
    assert "analysis_run_id" in result
    run = db_session.get(AnalysisRun, uuid.UUID(result["analysis_run_id"]))
    assert run is not None
    assert run.status == "DEFERRED"

    # Historical coverage -> SKIPPED on second old-task delivery.
    again = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(doc_a.id)],
        new_document_ids=[str(doc_a.id)],
        attempt=3,
    )
    assert again["status"] == "SKIPPED"
    _cleanup_person(db_session, person.id, admin.id)
