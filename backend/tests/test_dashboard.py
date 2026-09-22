"""Dashboard operational summary API tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(redis_prefix: str) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"

    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app
    from app.storage.s3 import reset_object_storage_cache

    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _create_user(db_session, *, login_id: str, password: str = "Secret123!", role: str = "USER"):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id,
        password_hash=hash_password(password),
        name="Dash Tester",
        role=role,
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _cleanup_user(db_session, user_id) -> None:
    from app.db.models.revision import AuditLog
    from app.db.models.user import AppUser

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _login(client: TestClient, login_id: str, password: str = "Secret123!") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


def _ensure_doc_type(db_session) -> str:
    from app.db.models.code import CodeMaster

    code = "DOC-RESUME"
    if db_session.get(CodeMaster, code) is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type="DOC_TYPE",
                name="이력서",
                sort_order=0,
                is_active=True,
            )
        )
        db_session.commit()
    return code


def _cleanup_person(db_session, person_id) -> None:
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision

    run_ids = list(
        db_session.execute(
            select(AnalysisRun.id).where(AnalysisRun.person_id == person_id)
        )
        .scalars()
        .all()
    )
    if run_ids:
        db_session.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id.in_(run_ids))
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(
                AnalysisRunDocument.analysis_run_id.in_(run_ids)
            )
        )
        db_session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))

    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        )
        .scalars()
        .all()
    )
    if group_ids:
        doc_ids = list(
            db_session.execute(
                select(Document.id).where(Document.document_group_id.in_(group_ids))
            )
            .scalars()
            .all()
        )
        if doc_ids:
            db_session.execute(
                delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids))
            )
            db_session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(group_ids)))

    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "PERSON", AuditLog.target_id == person_id
        )
    )
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.commit()


def _add_person(
    db_session,
    *,
    status: str = "ACTIVE",
    name: str = "테스트인력",
    created_at: datetime | None = None,
    deleted_at: datetime | None = None,
    affiliation: str | None = "테스트소속",
    grade: str | None = "EXPERT",
):
    from app.db.models.person import Person, PersonProfile

    person = Person(status=status, deleted_at=deleted_at)
    db_session.add(person)
    db_session.flush()
    if created_at is not None:
        person.created_at = created_at
    profile = PersonProfile(
        person_id=person.id,
        name=name,
        affiliation_company=affiliation,
        technical_grade=grade,
        profile_version=1,
    )
    db_session.add(profile)
    db_session.flush()
    return person


def _add_analysis(
    db_session,
    person_id,
    *,
    status: str = "REVIEWING",
    created_at: datetime | None = None,
):
    from app.db.models.analysis import AnalysisRun

    run = AnalysisRun(
        person_id=person_id,
        status=status,
        candidate_json={},
        base_profile_version=1,
    )
    db_session.add(run)
    db_session.flush()
    if created_at is not None:
        run.created_at = created_at
    return run


def _add_diff(
    db_session,
    run_id,
    *,
    change_type: str = "NEW",
    review_status: str = "PENDING",
    field_name: str = "name",
):
    from app.db.models.analysis import AnalysisDiffItem

    item = AnalysisDiffItem(
        analysis_run_id=run_id,
        entity_type="PROFILE",
        field_name=field_name,
        candidate_path=f"profile.{field_name}",
        change_type=change_type,
        review_status=review_status,
        new_value="x",
    )
    db_session.add(item)
    db_session.flush()
    return item


def _add_failed_document(
    db_session,
    person_id,
    *,
    filename: str = "fail.pdf",
    error: str = "parse failed",
    updated_at: datetime | None = None,
    deleted_doc: bool = False,
    deleted_group: bool = False,
):
    from app.db.models.document import Document, DocumentGroup

    _ensure_doc_type(db_session)
    group = DocumentGroup(
        person_id=person_id,
        document_type_code="DOC-RESUME",
        title="이력서",
        deleted_at=datetime.now(UTC) if deleted_group else None,
    )
    db_session.add(group)
    db_session.flush()
    # Set updated_at on INSERT only — post-flush assignment triggers
    # trg_document_updated_at which forces NEW.updated_at = NOW().
    doc_kwargs: dict = {
        "document_group_id": group.id,
        "version_no": 1,
        "is_latest": True,
        "original_filename": filename,
        "extension": "pdf",
        "mime_type": "application/pdf",
        "file_size": 10,
        "storage_key": f"test/dash/{uuid.uuid4()}.pdf",
        "sha256": uuid.uuid4().hex + uuid.uuid4().hex[:32],
        "processing_status": "FAILED",
        "processing_error": error,
        "deleted_at": datetime.now(UTC) if deleted_doc else None,
    }
    if updated_at is not None:
        doc_kwargs["updated_at"] = updated_at
    doc = Document(**doc_kwargs)
    db_session.add(doc)
    db_session.flush()
    return doc


def test_dashboard_requires_auth(client: TestClient):
    assert client.get("/api/v1/dashboard").status_code == 401


def test_user_dashboard_hides_admin_sections(client: TestClient, db_session):
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"du_{suffix}", role="USER")
    now = datetime.now(UTC)
    people = []
    try:
        people.append(
            _add_person(
                db_session,
                status="ACTIVE",
                name=f"Active_{suffix}",
                created_at=now - timedelta(days=1),
            )
        )
        people.append(
            _add_person(
                db_session,
                status="INACTIVE",
                name=f"Inactive_{suffix}",
                created_at=now - timedelta(days=2),
            )
        )
        people.append(
            _add_person(
                db_session,
                status="ARCHIVED",
                name=f"Archived_{suffix}",
                created_at=now - timedelta(days=3),
            )
        )
        deleted = _add_person(
            db_session,
            status="DELETED",
            name=f"Deleted_{suffix}",
            created_at=now,
            deleted_at=now,
        )
        people.append(deleted)
        soft = _add_person(
            db_session,
            status="ACTIVE",
            name=f"Soft_{suffix}",
            created_at=now,
            deleted_at=now,
        )
        people.append(soft)
        # admin-only noise
        run = _add_analysis(db_session, people[0].id, status="REVIEWING")
        _add_diff(db_session, run.id)
        _add_failed_document(db_session, people[0].id)
        db_session.commit()

        _login(client, user.login_id)
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["permissions"] == {
            "can_manage_people": False,
            "can_manage_analyses": False,
        }
        assert data["analysis"] is None
        assert data["recent_analyses"] is None
        assert data["document_failures"] is None

        people_summary = data["people"]
        # counts are global; assert our seeded statuses contribute and deleted excluded
        # by checking recent_people does not include deleted names
        recent_names = [p["name"] for p in data["recent_people"]]
        assert f"Deleted_{suffix}" not in recent_names
        assert f"Soft_{suffix}" not in recent_names
        assert people_summary["total"] >= 3
        assert people_summary["active"] >= 1
        assert people_summary["inactive"] >= 1
        assert people_summary["archived"] >= 1
        assert people_summary["total"] == (
            people_summary["active"]
            + people_summary["inactive"]
            + people_summary["archived"]
        )
        assert len(data["recent_people"]) <= 5
        # newest non-deleted among our fixtures should appear near top
        assert any(p["name"] == f"Active_{suffix}" for p in data["recent_people"])
    finally:
        for p in people:
            _cleanup_person(db_session, p.id)
        csrf = client.cookies.get("ts_csrf")
        if csrf:
            client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _cleanup_user(db_session, user.id)


def test_admin_dashboard_analysis_and_documents(client: TestClient, db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"da_{suffix}", role="ADMIN")
    now = datetime.now(UTC)
    people = []
    try:
        p1 = _add_person(db_session, name=f"P1_{suffix}", created_at=now - timedelta(hours=5))
        p2 = _add_person(db_session, name=f"P2_{suffix}", created_at=now - timedelta(hours=4))
        people.extend([p1, p2])

        runs = [
            _add_analysis(db_session, p1.id, status="QUEUED", created_at=now - timedelta(hours=3)),
            _add_analysis(
                db_session, p1.id, status="PROCESSING", created_at=now - timedelta(hours=2)
            ),
            _add_analysis(
                db_session, p2.id, status="REVIEWING", created_at=now - timedelta(hours=1)
            ),
            _add_analysis(db_session, p2.id, status="FAILED", created_at=now - timedelta(minutes=30)),
            _add_analysis(
                db_session, p1.id, status="CONFIRMED", created_at=now - timedelta(minutes=20)
            ),
            _add_analysis(
                db_session, p2.id, status="CANCELLED", created_at=now - timedelta(minutes=10)
            ),
        ]
        reviewing = runs[2]
        _add_diff(db_session, reviewing.id, change_type="NEW", review_status="PENDING")
        _add_diff(db_session, reviewing.id, change_type="UPDATE", review_status="PENDING")
        _add_diff(db_session, reviewing.id, change_type="SAME", review_status="PENDING")
        _add_diff(db_session, reviewing.id, change_type="CONFLICT", review_status="ACCEPTED")
        _add_diff(db_session, reviewing.id, change_type="REVIEW", review_status="PENDING")

        # older failed vs newer failed
        _add_failed_document(
            db_session,
            p1.id,
            filename=f"old_{suffix}.pdf",
            error="old error",
            updated_at=now + timedelta(days=400),
        )
        _add_failed_document(
            db_session,
            p2.id,
            filename=f"new_{suffix}.pdf",
            error="new error",
            updated_at=now + timedelta(days=401),
        )
        # excluded
        _add_failed_document(
            db_session, p1.id, filename=f"deldoc_{suffix}.pdf", deleted_doc=True
        )
        ready_group_person = p1
        from app.db.models.document import Document, DocumentGroup

        _ensure_doc_type(db_session)
        g = DocumentGroup(
            person_id=ready_group_person.id,
            document_type_code="DOC-RESUME",
            title="ready",
        )
        db_session.add(g)
        db_session.flush()
        db_session.add(
            Document(
                document_group_id=g.id,
                version_no=1,
                is_latest=True,
                original_filename=f"ready_{suffix}.pdf",
                extension="pdf",
                mime_type="application/pdf",
                file_size=1,
                storage_key=f"test/dash/{uuid.uuid4()}.pdf",
                sha256="d" * 64,
                processing_status="READY",
            )
        )
        db_session.commit()

        _login(client, admin.login_id)
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["permissions"]["can_manage_analyses"] is True
        assert data["analysis"] is not None
        analysis = data["analysis"]
        assert analysis["queued"] >= 1
        assert analysis["processing"] >= 1
        assert analysis["reviewing"] >= 1
        assert analysis["failed"] >= 1
        assert analysis["review_pending_runs"] == analysis["reviewing"]

        recent = data["recent_analyses"]
        assert recent is not None
        assert len(recent) <= 5
        # newest first among our runs (CANCELLED is newest)
        assert recent[0]["status"] in {
            "CANCELLED",
            "CONFIRMED",
            "FAILED",
            "REVIEWING",
            "PROCESSING",
            "QUEUED",
        }
        reviewing_item = next(r for r in recent if r["analysis_id"] == str(reviewing.id))
        # NEW + UPDATE + REVIEW PENDING = 3; SAME PENDING excluded; CONFLICT ACCEPTED excluded
        assert reviewing_item["pending_count"] == 3

        failures = data["document_failures"]
        assert failures is not None
        filenames = [f["original_filename"] for f in failures]
        assert f"new_{suffix}.pdf" in filenames
        assert f"ready_{suffix}.pdf" not in filenames
        assert f"deldoc_{suffix}.pdf" not in filenames
        # newest failed first among our two
        new_idx = filenames.index(f"new_{suffix}.pdf")
        old_idx = filenames.index(f"old_{suffix}.pdf")
        assert new_idx < old_idx
    finally:
        for p in people:
            _cleanup_person(db_session, p.id)
        csrf = client.cookies.get("ts_csrf")
        if csrf:
            client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _cleanup_user(db_session, admin.id)


def test_recent_people_order_and_limit(client: TestClient, db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"dr_{suffix}", role="ADMIN")
    now = datetime.now(UTC)
    people = []
    try:
        for i in range(7):
            people.append(
                _add_person(
                    db_session,
                    name=f"Ord_{suffix}_{i}",
                    # Far-future timestamps so these dominate recent_people top-5.
                    created_at=now + timedelta(days=400) + timedelta(minutes=i),
                )
            )
        db_session.commit()
        _login(client, admin.login_id)
        data = client.get("/api/v1/dashboard").json()["data"]
        recent = data["recent_people"]
        assert len(recent) <= 5
        our = [p for p in recent if p["name"] and p["name"].startswith(f"Ord_{suffix}_")]
        assert len(our) == 5
        # created_at desc → highest i first
        assert our[0]["name"] == f"Ord_{suffix}_6"
        assert our[4]["name"] == f"Ord_{suffix}_2"
    finally:
        for p in people:
            _cleanup_person(db_session, p.id)
        csrf = client.cookies.get("ts_csrf")
        if csrf:
            client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _cleanup_user(db_session, admin.id)


def test_admin_dashboard_excludes_deleted_person_analyses(client: TestClient, db_session):
    """DELETED / soft-deleted Person AnalysisRun must not affect ops KPIs or recent list."""
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"dd_{suffix}", role="ADMIN")
    now = datetime.now(UTC)
    people = []
    try:
        visible = _add_person(
            db_session,
            name=f"Visible_{suffix}",
            created_at=now - timedelta(hours=1),
        )
        people.append(visible)
        visible_run = _add_analysis(
            db_session,
            visible.id,
            status="REVIEWING",
            created_at=now + timedelta(days=500),
        )
        db_session.commit()

        _login(client, admin.login_id)
        before = client.get("/api/v1/dashboard").json()["data"]
        before_analysis = before["analysis"]
        assert before_analysis is not None

        status_deleted = _add_person(
            db_session,
            status="DELETED",
            name=f"StatusDel_{suffix}",
            created_at=now,
            deleted_at=now,
        )
        soft_deleted = _add_person(
            db_session,
            status="ACTIVE",
            name=f"SoftDel_{suffix}",
            created_at=now,
            deleted_at=now,
        )
        people.extend([status_deleted, soft_deleted])

        hidden_runs = [
            _add_analysis(
                db_session,
                status_deleted.id,
                status="REVIEWING",
                created_at=now + timedelta(days=501),
            ),
            _add_analysis(
                db_session,
                status_deleted.id,
                status="FAILED",
                created_at=now + timedelta(days=502),
            ),
            _add_analysis(
                db_session,
                soft_deleted.id,
                status="REVIEWING",
                created_at=now + timedelta(days=503),
            ),
            _add_analysis(
                db_session,
                soft_deleted.id,
                status="FAILED",
                created_at=now + timedelta(days=504),
            ),
        ]
        db_session.commit()

        after = client.get("/api/v1/dashboard").json()["data"]
        after_analysis = after["analysis"]
        assert after_analysis is not None
        assert after_analysis["queued"] == before_analysis["queued"]
        assert after_analysis["processing"] == before_analysis["processing"]
        assert after_analysis["reviewing"] == before_analysis["reviewing"]
        assert after_analysis["failed"] == before_analysis["failed"]
        assert after_analysis["review_pending_runs"] == before_analysis["review_pending_runs"]

        recent_ids = {r["analysis_id"] for r in (after["recent_analyses"] or [])}
        for run in hidden_runs:
            assert str(run.id) not in recent_ids
        assert str(visible_run.id) in recent_ids
    finally:
        for p in people:
            _cleanup_person(db_session, p.id)
        csrf = client.cookies.get("ts_csrf")
        if csrf:
            client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _cleanup_user(db_session, admin.id)
