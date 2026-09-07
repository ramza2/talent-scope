"""Document upload / storage API tests — uses in-memory ObjectStorage (APP_ENV=test)."""

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


def _create_user(db_session, *, login_id: str, password: str, role: str = "USER"):
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
    from app.db.models.upload import UploadSession, UploadTempFile

    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        ).scalars().all()
    )
    for gid in group_ids:
        db_session.execute(delete(Document).where(Document.document_group_id == gid))
    db_session.execute(delete(DocumentGroup).where(DocumentGroup.person_id == person_id))
    db_session.execute(
        delete(UploadSession).where(UploadSession.resolved_person_id == person_id)
    )
    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == person_id))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.commit()


def _cleanup_session(db_session, session_id) -> None:
    from app.db.models.revision import AuditLog
    from app.db.models.upload import UploadSession, UploadTempFile

    db_session.execute(
        delete(UploadTempFile).where(UploadTempFile.upload_session_id == session_id)
    )
    db_session.execute(delete(UploadSession).where(UploadSession.id == session_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "UPLOAD_SESSION", AuditLog.target_id == session_id
        )
    )
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
    response = client.post(
        "/api/v1/people",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def test_upload_session_document_flow(client: TestClient, db_session) -> None:
    from app.db.models.document import Document
    from app.db.models.revision import AuditLog
    from app.storage.s3 import build_object_storage

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}", f"DOC-OTHER-{suffix}", f"JOB-X-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    _ensure_code(db_session, codes[1], "DOC_TYPE", "기타")
    _ensure_code(db_session, codes[2], "JOB", "NotDoc")

    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    person_id = None
    session_id = None
    try:
        # USER cannot create session
        user_csrf = _login(client, user.login_id)
        assert (
            client.post(
                "/api/v1/upload-sessions",
                headers={"X-CSRF-Token": user_csrf},
                json={},
            ).status_code
            == 403
        )

        csrf = _login(client, admin.login_id)
        # CSRF required
        assert client.post("/api/v1/upload-sessions", json={}).status_code == 403

        created = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={"target_person_id": None},
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "UPLOADING"

        person_id = _create_person(client, csrf, f"문서인력_{suffix}")

        pdf_bytes = b"%PDF-1.4 test-content-" + suffix.encode()
        sha = hashlib.sha256(pdf_bytes).hexdigest()

        upload = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("이력서_테스트.pdf", io.BytesIO(pdf_bytes), "application/pdf"))],
        )
        assert upload.status_code == 201, upload.text
        file_data = upload.json()["data"][0]
        file_id = file_data["temp_file_id"]
        assert file_data["sha256"] == sha
        assert file_data["file_size"] == len(pdf_bytes)
        assert file_data["extension"] == "pdf"

        storage = build_object_storage()
        assert storage.exists(f"temp/{session_id}/{file_id}")

        # unsupported extension
        bad = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("malware.exe", io.BytesIO(b"MZ"), "application/octet-stream"))],
        )
        assert bad.status_code == 415

        # empty file
        empty = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("empty.pdf", io.BytesIO(b""), "application/pdf"))],
        )
        assert empty.status_code == 400

        # invalid DOC_TYPE
        bad_type = client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{file_id}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[2]},
        )
        assert bad_type.status_code == 400

        patched = client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{file_id}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["data"][0]["document_type_code"] == codes[0]

        # identify not implemented
        assert (
            client.post(
                f"/api/v1/upload-sessions/{session_id}/identify",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 501
        )

        # CREATE_NEW not implemented
        assert (
            client.post(
                f"/api/v1/upload-sessions/{session_id}/resolve",
                headers={"X-CSRF-Token": csrf},
                json={
                    "mode": "CREATE_NEW",
                    "document_resolution": [
                        {"temp_file_id": file_id, "mode": "NEW_GROUP"}
                    ],
                },
            ).status_code
            == 501
        )

        # LINK_EXISTING NEW_GROUP
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
        assert resolved.json()["data"]["person_id"] == person_id

        # temp gone, permanent exists
        assert not storage.exists(f"temp/{session_id}/{file_id}")
        db_session.expire_all()
        doc = db_session.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        assert doc.version_no == 1
        assert doc.is_latest is True
        assert doc.processing_status == "UPLOADED"
        assert storage.exists(doc.storage_key)

        # list / detail
        listed = client.get(f"/api/v1/people/{person_id}/documents")
        assert listed.status_code == 200
        assert len(listed.json()["data"]) == 1
        detail = client.get(f"/api/v1/documents/{doc_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["original_filename"] == "이력서_테스트.pdf"

        # download
        dl = client.get(f"/api/v1/documents/{doc_id}/download")
        assert dl.status_code == 200
        assert dl.content == pdf_bytes
        assert "attachment" in dl.headers.get("content-disposition", "")
        audit = db_session.execute(
            select(AuditLog).where(
                AuditLog.action_type == "DOCUMENT_DOWNLOAD",
                AuditLog.target_id == uuid.UUID(doc_id),
            )
        ).scalar_one()
        assert audit is not None

        # preview PDF inline
        prev = client.get(f"/api/v1/documents/{doc_id}/preview")
        assert prev.status_code == 200
        assert "inline" in prev.headers.get("content-disposition", "")
        assert prev.content == pdf_bytes

        # Range
        ranged = client.get(
            f"/api/v1/documents/{doc_id}/preview",
            headers={"Range": "bytes=0-3"},
        )
        assert ranged.status_code == 206
        assert ranged.content == pdf_bytes[:4]
        assert "bytes 0-3/" in ranged.headers.get("content-range", "")

        # person document_summary
        person = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert person["document_summary"]["count"] == 1

        # NEW_VERSION via new session
        session2 = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        pdf2 = b"%PDF-1.4 v2-" + suffix.encode()
        up2 = client.post(
            f"/api/v1/upload-sessions/{session2}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("이력서_v2.pdf", io.BytesIO(pdf2), "application/pdf"))],
        )
        assert up2.status_code == 201
        file2 = up2.json()["data"][0]["temp_file_id"]
        group_id = detail.json()["data"]["document_group_id"]
        client.patch(
            f"/api/v1/upload-sessions/{session2}/files/{file2}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
        )
        res2 = client.post(
            f"/api/v1/upload-sessions/{session2}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "LINK_EXISTING",
                "person_id": person_id,
                "document_resolution": [
                    {
                        "temp_file_id": file2,
                        "mode": "NEW_VERSION",
                        "document_group_id": group_id,
                        "document_type_code": codes[0],
                    }
                ],
            },
        )
        assert res2.status_code == 201, res2.text
        doc2_id = res2.json()["data"]["document_ids"][0]
        db_session.expire_all()
        d1 = db_session.get(Document, uuid.UUID(doc_id))
        d2 = db_session.get(Document, uuid.UUID(doc2_id))
        assert d1.is_latest is False
        assert d2.is_latest is True
        assert d2.version_no == 2

        versions = client.get(f"/api/v1/document-groups/{group_id}/versions")
        assert versions.status_code == 200
        assert len(versions.json()["data"]) == 2

        # soft delete latest → previous becomes latest
        assert (
            client.delete(
                f"/api/v1/documents/{doc2_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        # Soft-deleted: USER would 404; ADMIN can still read metadata for restore UX.
        db_session.expire_all()
        d1 = db_session.get(Document, uuid.UUID(doc_id))
        assert d1.is_latest is True
        person = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert person["document_summary"]["count"] == 1

        adm_detail = client.get(f"/api/v1/documents/{doc2_id}")
        assert adm_detail.status_code == 200, adm_detail.text
        assert adm_detail.json()["data"]["deleted_at"] is not None

        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        assert client.get(f"/api/v1/documents/{doc2_id}").status_code == 404
        csrf = _login(client, admin.login_id)

        # restore
        restored = client.post(
            f"/api/v1/documents/{doc2_id}/restore",
            headers={"X-CSRF-Token": csrf},
        )
        assert restored.status_code == 200
        db_session.expire_all()
        d2 = db_session.get(Document, uuid.UUID(doc2_id))
        assert d2.deleted_at is None
        assert d2.is_latest is True  # highest version among active
        person = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert person["document_summary"]["count"] == 2

        # DELETED person visibility
        client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "DELETED"},
        )
        assert client.get(f"/api/v1/people/{person_id}/documents").status_code == 200
        assert client.get(f"/api/v1/documents/{doc_id}").status_code == 200
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        assert client.get(f"/api/v1/people/{person_id}/documents").status_code == 404
        assert client.get(f"/api/v1/documents/{doc_id}").status_code == 404
        assert client.get(f"/api/v1/documents/{doc_id}/download").status_code == 404

        # temp delete + cancel on fresh session
        csrf = _login(client, admin.login_id)
        s3 = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        up = client.post(
            f"/api/v1/upload-sessions/{s3}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("t.pdf", io.BytesIO(b"%PDF-x"), "application/pdf"))],
        )
        tid = up.json()["data"][0]["temp_file_id"]
        assert storage.exists(f"temp/{s3}/{tid}")
        assert (
            client.delete(
                f"/api/v1/upload-sessions/{s3}/files/{tid}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert not storage.exists(f"temp/{s3}/{tid}")

        up = client.post(
            f"/api/v1/upload-sessions/{s3}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("t2.pdf", io.BytesIO(b"%PDF-y"), "application/pdf"))],
        )
        tid2 = up.json()["data"][0]["temp_file_id"]
        assert (
            client.delete(
                f"/api/v1/upload-sessions/{s3}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert not storage.exists(f"temp/{s3}/{tid2}")
        assert client.get(f"/api/v1/upload-sessions/{s3}").json()["data"]["status"] == "CANCELLED"

        # preview unavailable for docx without preview_storage_key
        # (covered conceptually — skip creating docx object if promote needs DOC_TYPE)

        _cleanup_session(db_session, uuid.UUID(session2))
        _cleanup_session(db_session, uuid.UUID(s3))
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        if session_id:
            _cleanup_session(db_session, uuid.UUID(session_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        _cleanup_user(db_session, user.id)
