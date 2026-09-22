"""Document preview_available metadata vs /preview endpoint semantics."""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Generator

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
def client(redis_prefix: str, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"

    monkeypatch.setattr(
        "app.tasks.document_tasks.process_document.delay",
        lambda *_a, **_k: None,
    )

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

    from app.storage.s3 import build_object_storage

    storage = build_object_storage()
    if hasattr(storage, "clear"):
        storage.clear()
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


def _create_user(db_session, *, login_id: str, password: str, role: str = "ADMIN"):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id,
        password_hash=hash_password(password),
        name="Tester",
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
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _ensure_code(db_session, code: str, code_type: str, name: str) -> None:
    from app.db.models.code import CodeMaster

    if db_session.get(CodeMaster, code) is None:
        db_session.add(
            CodeMaster(
                code=code, code_type=code_type, name=name, sort_order=0, is_active=True
            )
        )
        db_session.commit()


def _cleanup_codes(db_session, codes: list[str]) -> None:
    from app.db.models.code import CodeMaster

    for code in codes:
        db_session.execute(delete(CodeMaster).where(CodeMaster.code == code))
    db_session.commit()


def _cleanup_person(db_session, person_id) -> None:
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        )
        .scalars()
        .all()
    )
    for gid in group_ids:
        db_session.execute(delete(Document).where(Document.document_group_id == gid))
    db_session.execute(delete(DocumentGroup).where(DocumentGroup.person_id == person_id))
    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == person_id))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
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


def _create_person(client: TestClient, csrf: str, name: str) -> str:
    resp = client.post(
        "/api/v1/people",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _seed_document(
    db_session,
    *,
    person_id,
    user_id,
    doc_type: str,
    extension: str,
    processing_status: str,
    preview_storage_key: str | None = None,
    filename: str | None = None,
    put_storage: bool = False,
    put_preview: bool = False,
):
    from app.db.models.document import Document, DocumentGroup
    from app.storage.s3 import build_object_storage

    group = DocumentGroup(
        person_id=person_id,
        document_type_code=doc_type,
        title=f"doc-{extension}",
    )
    db_session.add(group)
    db_session.flush()
    body = b"%PDF-1.4 seed-" + uuid.uuid4().hex.encode() if extension == "pdf" else b"x" * 32
    storage_key = f"documents/{person_id}/{group.id}/{uuid.uuid4()}.bin"
    preview_key = preview_storage_key
    if put_preview and preview_key is None:
        preview_key = f"documents/{person_id}/{group.id}/preview.pdf"
    doc = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename=filename or f"file.{extension}",
        extension=extension,
        mime_type="application/octet-stream",
        file_size=len(body),
        storage_key=storage_key,
        sha256=hashlib.sha256(body).hexdigest(),
        preview_storage_key=preview_key,
        processing_status=processing_status,
        uploaded_by=user_id,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)

    storage = build_object_storage()
    if put_storage:
        storage.put_bytes(storage_key, body, content_type="application/octet-stream")
    if put_preview and preview_key:
        storage.put_bytes(preview_key, b"%PDF-1.4 preview", content_type="application/pdf")
    return doc


def test_inline_pdf_preview_available_without_generated_key(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"pa_{suffix}", password="Secret123!")
    person_id = None
    session_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"prev_{suffix}")
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        pdf = b"%PDF-1.4 inline-" + suffix.encode()
        up = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("a.pdf", io.BytesIO(pdf), "application/pdf"))],
        )
        assert up.status_code == 201, up.text
        file_id = up.json()["data"][0]["temp_file_id"]
        resolved = client.post(
            f"/api/v1/upload-sessions/{session_id}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "LINK_EXISTING",
                "person_id": person_id,
                "document_resolution": [
                    {
                        "temp_file_id": file_id,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes[0],
                        "title": "이력서",
                    }
                ],
            },
        )
        assert resolved.status_code == 201, resolved.text
        doc_id = resolved.json()["data"]["document_ids"][0]

        listed = client.get(f"/api/v1/people/{person_id}/documents")
        assert listed.status_code == 200
        row = listed.json()["data"][0]
        assert row["preview_available"] is True
        assert row["processing_status"] == "UPLOADED"

        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["preview_available"] is True
        assert detail.json()["data"]["preview_storage_key"] is None

        prev = client.get(f"/api/v1/documents/{doc_id}/preview")
        assert prev.status_code == 200
        assert prev.content == pdf
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_ready_without_preview_key_is_unavailable(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"pb_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"hwp_{suffix}")
        doc = _seed_document(
            db_session,
            person_id=uuid.UUID(person_id),
            user_id=admin.id,
            doc_type=codes[0],
            extension="hwp",
            processing_status="READY",
            preview_storage_key=None,
        )

        listed = client.get(f"/api/v1/people/{person_id}/documents")
        assert listed.status_code == 200
        row = next(r for r in listed.json()["data"] if r["document_id"] == str(doc.id))
        assert row["processing_status"] == "READY"
        assert row["preview_available"] is False

        detail = client.get(f"/api/v1/documents/{doc.id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["preview_available"] is False

        prev = client.get(f"/api/v1/documents/{doc.id}/preview")
        assert prev.status_code == 409
        assert prev.json()["code"] == "PREVIEW_UNAVAILABLE"
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_generated_preview_key_makes_available(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"pc_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"docx_{suffix}")
        doc = _seed_document(
            db_session,
            person_id=uuid.UUID(person_id),
            user_id=admin.id,
            doc_type=codes[0],
            extension="docx",
            processing_status="READY",
            put_preview=True,
        )
        listed = client.get(f"/api/v1/people/{person_id}/documents")
        row = next(r for r in listed.json()["data"] if r["document_id"] == str(doc.id))
        assert row["preview_available"] is True

        detail = client.get(f"/api/v1/documents/{doc.id}")
        assert detail.json()["data"]["preview_available"] is True
        assert detail.json()["data"]["preview_storage_key"]
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_failed_with_preserved_preview_is_available(
    client: TestClient, db_session
) -> None:
    from app.modules.documents.service import DocumentService
    from app.storage.s3 import build_object_storage

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"pd_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"fail_{suffix}")
        doc = _seed_document(
            db_session,
            person_id=uuid.UUID(person_id),
            user_id=admin.id,
            doc_type=codes[0],
            extension="pptx",
            processing_status="FAILED",
            put_preview=True,
        )
        svc = DocumentService(db_session, storage=build_object_storage())
        assert svc._preview_available(doc) is True

        listed = client.get(f"/api/v1/people/{person_id}/documents")
        row = next(r for r in listed.json()["data"] if r["document_id"] == str(doc.id))
        assert row["processing_status"] == "FAILED"
        assert row["preview_available"] is True
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_list_detail_preview_available_parity(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"pe_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"parity_{suffix}")
        docs = [
            _seed_document(
                db_session,
                person_id=uuid.UUID(person_id),
                user_id=admin.id,
                doc_type=codes[0],
                extension="pptx",
                processing_status="READY",
                preview_storage_key=None,
            ),
            _seed_document(
                db_session,
                person_id=uuid.UUID(person_id),
                user_id=admin.id,
                doc_type=codes[0],
                extension="png",
                processing_status="PROCESSING",
                preview_storage_key=None,
            ),
        ]
        listed = {
            r["document_id"]: r["preview_available"]
            for r in client.get(f"/api/v1/people/{person_id}/documents").json()["data"]
        }
        for doc in docs:
            detail = client.get(f"/api/v1/documents/{doc.id}").json()["data"]
            assert detail["preview_available"] == listed[str(doc.id)]
        assert listed[str(docs[0].id)] is False
        assert listed[str(docs[1].id)] is True
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
