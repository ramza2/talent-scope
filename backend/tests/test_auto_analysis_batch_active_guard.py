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
    active_idx = source.find("get_active_run_for_person(payload.person_id)")
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


def test_unrelated_active_run_defers_auto_batch(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.core.exceptions import AnalysisStateConflictError
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import document_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    defer_calls: list[dict] = []

    def _capture_defer(batch, new_ids, *, attempt=1):
        defer_calls.append(
            {
                "batch": list(batch),
                "new": list(new_ids),
                "attempt": attempt,
            }
        )

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
    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )
    assert len(_count_person_runs(db_session, person.id)) == 1

    batch = [doc_a.id, doc_b.id]
    with pytest.raises(AnalysisStateConflictError):
        service.create_analysis_for_ready_documents(
            batch, new_document_ids=batch
        )
    db_session.rollback()
    assert len(_count_person_runs(db_session, person.id)) == 1

    document_tasks._maybe_start_auto_profile_analysis(
        db_session,
        doc_a.id,
        batch_document_ids=batch,
        new_document_ids=batch,
    )
    assert len(defer_calls) == 1
    assert set(defer_calls[0]["batch"]) == set(batch)
    assert defer_calls[0]["attempt"] == 1
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_retry_reschedules_while_active(
    db_session, monkeypatch: pytest.MonkeyPatch
):
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
        analysis_tasks.retry_auto_profile_analysis_batch,
        "apply_async",
        _capture_apply_async,
    )

    admin = _create_user(
        db_session, login_id=f"dr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )

    result = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(doc_a.id)],
        new_document_ids=[str(doc_a.id)],
        attempt=1,
    )
    assert result["status"] == "DEFERRED"
    assert len(scheduled) == 1
    assert scheduled[0]["countdown"] == analysis_tasks.AUTO_ANALYSIS_DEFER_COUNTDOWN_SECONDS
    assert scheduled[0]["kwargs"]["attempt"] == 2
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)


def test_deferred_retry_creates_batch_after_active_clears(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun, AnalysisRunDocument
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        analysis_tasks.retry_auto_profile_analysis_batch,
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
    run = db_session.get(AnalysisRun, active.analysis_id)
    assert run is not None
    run.status = "REVIEWING"
    db_session.commit()

    batch = [doc_a.id, doc_b.id]
    result = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(i) for i in batch],
        new_document_ids=[str(i) for i in batch],
        attempt=3,
    )
    assert result["status"] == "QUEUED"
    runs = _count_person_runs(db_session, person.id)
    assert len(runs) == 2
    new_run_id = uuid.UUID(result["analysis_run_id"])
    linked = set(
        db_session.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == new_run_id
            )
        )
        .scalars()
        .all()
    )
    assert linked == set(batch)
    _cleanup_person(db_session, person.id, admin.id)


def test_duplicate_deferred_retry_keeps_single_run(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    admin = _create_user(
        db_session, login_id=f"dd_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, doc_a = _seed_person_with_ready_doc(db_session, admin.id)
    doc_b = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    batch = [doc_a.id, doc_b.id]
    first = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(i) for i in batch],
        new_document_ids=[str(i) for i in batch],
        attempt=1,
    )
    assert first["status"] == "QUEUED"
    second = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(i) for i in batch],
        new_document_ids=[str(i) for i in batch],
        attempt=2,
    )
    assert second["status"] == "SKIPPED"
    assert len(_count_person_runs(db_session, person.id)) == 1
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


def test_defer_max_attempt_does_not_reschedule(
    db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.modules.analysis.schemas import CreateAnalysisRequest
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from app.tasks import analysis_tasks

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis", lambda *_a, **_k: None
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        analysis_tasks.retry_auto_profile_analysis_batch,
        "apply_async",
        lambda *_a, **kwargs: scheduled.append(kwargs),
    )

    admin = _create_user(
        db_session, login_id=f"mxa_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, unrelated = _seed_person_with_ready_doc(db_session, admin.id)
    doc_a = _seed_second_ready_doc(db_session, person, admin.id, status="READY")
    service = AnalysisService(db_session, storage=get_object_storage())
    service.create_analysis(
        CreateAnalysisRequest(
            person_id=person.id,
            document_ids=[unrelated.id],
            analysis_type="PROFILE",
        ),
        admin.id,
    )

    result = analysis_tasks.retry_auto_profile_analysis_batch.run(
        batch_document_ids=[str(doc_a.id)],
        new_document_ids=[str(doc_a.id)],
        attempt=analysis_tasks.AUTO_ANALYSIS_DEFER_MAX_ATTEMPTS,
    )
    assert result["status"] == "DEFER_EXHAUSTED"
    assert scheduled == []
    assert len(_count_person_runs(db_session, person.id)) == 1
    _cleanup_person(db_session, person.id, admin.id)
