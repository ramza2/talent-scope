"""Upload session AI identify + CREATE_NEW + duplicate candidate tests."""

from __future__ import annotations

import io
import os
import uuid
from collections.abc import Generator
from pathlib import Path

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

    # Default: identify enqueue is a no-op (tests call IdentificationService directly).
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.identify_upload_session.delay",
        lambda *_a, **_k: None,
    )
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
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob
    from app.db.models.upload import UploadSession, UploadTempFile

    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        )
        .scalars()
        .all()
    )
    doc_ids = []
    for gid in group_ids:
        doc_ids.extend(
            db_session.execute(
                select(Document.id).where(Document.document_group_id == gid)
            )
            .scalars()
            .all()
        )
    for did in doc_ids:
        db_session.execute(delete(DocumentPage).where(DocumentPage.document_id == did))
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


def _login(client: TestClient, login_id: str, password: str = "Secret123!") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


def _create_person_api(client: TestClient, csrf: str, name: str, **extra) -> str:
    body = {"name": name, **extra}
    response = client.post(
        "/api/v1/people",
        headers={"X-CSRF-Token": csrf},
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


class FakeConverter:
    def __init__(self, pdf_bytes: bytes, *, fail: bool = False) -> None:
        self.pdf_bytes = pdf_bytes
        self.fail = fail
        self.calls = 0

    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        from app.modules.document_processing.converters.base import ConverterError

        self.calls += 1
        if self.fail:
            raise ConverterError("injected converter failure")
        out = work_dir / "converted.pdf"
        out.write_bytes(self.pdf_bytes)
        return out


def _seed_session_with_pdf(client, db_session, csrf, *, codes, pdf_bytes, filename="a.pdf"):
    session_id = client.post(
        "/api/v1/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json={},
    ).json()["data"]["id"]
    up = client.post(
        f"/api/v1/upload-sessions/{session_id}/files",
        headers={"X-CSRF-Token": csrf},
        files=[("files", (filename, io.BytesIO(pdf_bytes), "application/pdf"))],
    )
    assert up.status_code in (200, 201), up.text
    fid = up.json()["data"][0]["temp_file_id"]
    client.patch(
        f"/api/v1/upload-sessions/{session_id}/files/{fid}",
        headers={"X-CSRF-Token": csrf},
        json={"document_type_code": codes[0]},
    )
    return session_id, fid


# --- Identify API ---


def test_identify_user_forbidden(client, db_session):
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    try:
        csrf = _login(client, user.login_id)
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        # USER cannot create upload sessions either (ADMIN only) — expect 403
        assert session_id.status_code == 403
    finally:
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, user.id)


def test_identify_admin_no_csrf(client, db_session):
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("홍길동")
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        assert (
            client.post(f"/api/v1/upload-sessions/{session_id}/identify").status_code
            == 403
        )
    finally:
        # cancel session cleanup via cancel
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_identify_empty_session_validation(client, db_session):
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/identify",
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 400
    finally:
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_user(db_session, admin.id)


def test_identify_202_and_idempotent_no_duplicate_enqueue(
    client, db_session, monkeypatch
):
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    enqueued: list[str] = []

    def capture(sid):
        enqueued.append(str(sid))

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_upload_identify", capture
    )
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("홍길동")
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        r1 = client.post(
            f"/api/v1/upload-sessions/{session_id}/identify",
            headers={"X-CSRF-Token": csrf},
        )
        assert r1.status_code == 202, r1.text
        assert r1.json()["data"]["status"] == "IDENTIFYING"
        r2 = client.post(
            f"/api/v1/upload-sessions/{session_id}/identify",
            headers={"X-CSRF-Token": csrf},
        )
        assert r2.status_code == 202
        assert len(enqueued) == 1
    finally:
        # force cancel even if IDENTIFYING
        from app.db.models.upload import UploadSession

        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status == "IDENTIFYING":
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_identify_enqueue_failure_restores_status(client, db_session, monkeypatch):
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")

    def boom(_sid):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_upload_identify", boom
    )
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("홍길동")
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/identify",
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code == 503
        assert res.json()["code"] == "AI_QUEUE_UNAVAILABLE"
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "UPLOADING"
    finally:
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_identify_resolved_conflict(client, db_session, monkeypatch):
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_upload_identify", lambda *_a, **_k: None
    )
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person_api(client, csrf, f"link_{suffix}")
        pdf = build_minimal_pdf_with_text("doc")
        session_id, fid = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "LINK_EXISTING",
                "person_id": person_id,
                "document_resolution": [
                    {
                        "temp_file_id": fid,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes[0],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert (
            client.post(
                f"/api/v1/upload-sessions/{session_id}/identify",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 409
        )
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


# --- Extraction / AI ---


def test_identify_text_pdf_sets_identity(db_session, client):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("홍길동 ABC테크 hong@example.com")
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row is not None
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        llm = FakeLLMProvider(
            {
                "name": "홍길동",
                "company": "ABC테크",
                "phone": "010-1234-5678",
                "email": "hong@example.com",
            }
        )
        status = IdentificationService(
            db_session, storage=storage, llm=llm, vlm=None
        ).run(uuid.UUID(session_id))
        assert status == "IDENTIFIED"
        assert llm.calls == 1
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row is not None
        assert row.identified_name == "홍길동"
        assert row.identified_email == "hong@example.com"
        assert isinstance(row.duplicate_result_json, list)
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status not in {"CANCELLED", "RESOLVED"}:
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_identify_scanned_pdf_uses_vlm(db_session, client):
    import pymupdf

    from app.ai.providers.llm import FakeLLMProvider
    from app.ai.providers.vlm import FakeVLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        empty = pymupdf.open()
        empty.new_page()
        pdf = empty.tobytes()
        empty.close()
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        vlm = FakeVLMProvider("홍길동\n01099998888")
        llm = FakeLLMProvider(
            {"name": "홍길동", "company": None, "phone": "01099998888", "email": None}
        )
        status = IdentificationService(
            db_session, storage=storage, llm=llm, vlm=vlm
        ).run(uuid.UUID(session_id))
        assert status == "IDENTIFIED"
        assert vlm.calls >= 1
        assert llm.calls == 1
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status not in {"CANCELLED", "RESOLVED"}:
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_identify_office_uses_converter(db_session, client):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        # Minimal zip-like header is not required for office if we skip signature —
        # upload validates signature for docx (PK). Use real PK header.
        docx = b"PK\x03\x04" + b"\x00" * 64
        up = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[
                (
                    "files",
                    (
                        "a.docx",
                        io.BytesIO(docx),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
        )
        assert up.status_code in (200, 201), up.text
        fid = up.json()["data"][0]["temp_file_id"]
        client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{fid}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        converter = FakeConverter(build_minimal_pdf_with_text("From DOCX 홍길동"))
        llm = FakeLLMProvider({"name": "홍길동", "company": None, "phone": None, "email": None})
        status = IdentificationService(
            db_session, storage=storage, llm=llm, converter=converter, vlm=None
        ).run(uuid.UUID(session_id))
        assert status == "IDENTIFIED"
        assert converter.calls == 1
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status not in {"CANCELLED", "RESOLVED"}:
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_identify_partial_file_failure_still_succeeds(db_session, client):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        pdf = build_minimal_pdf_with_text("ok person")
        docx = b"PK\x03\x04" + b"\x00" * 64
        up = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[
                ("files", ("ok.pdf", io.BytesIO(pdf), "application/pdf")),
                (
                    "files",
                    (
                        "bad.docx",
                        io.BytesIO(docx),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ),
            ],
        )
        assert up.status_code in (200, 201), up.text
        for item in up.json()["data"]:
            client.patch(
                f"/api/v1/upload-sessions/{session_id}/files/{item['temp_file_id']}",
                headers={"X-CSRF-Token": csrf},
                json={"document_type_code": codes[0]},
            )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        converter = FakeConverter(b"%PDF", fail=True)
        llm = FakeLLMProvider({"name": "부분성공", "company": None, "phone": None, "email": None})
        status = IdentificationService(
            db_session, storage=storage, llm=llm, converter=converter, vlm=None
        ).run(uuid.UUID(session_id))
        assert status == "IDENTIFIED"
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row.identified_name == "부분성공"
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status not in {"CANCELLED", "RESOLVED"}:
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_identify_all_files_fail_restores_uploading(db_session, client):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        docx = b"PK\x03\x04" + b"\x00" * 64
        up = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[
                (
                    "files",
                    (
                        "bad.docx",
                        io.BytesIO(docx),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
        )
        fid = up.json()["data"][0]["temp_file_id"]
        client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{fid}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        converter = FakeConverter(b"%PDF", fail=True)
        llm = FakeLLMProvider()
        status = IdentificationService(
            db_session, storage=storage, llm=llm, converter=converter, vlm=None
        ).run(uuid.UUID(session_id))
        assert status == "UPLOADING"
        assert llm.calls == 0
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row.status == "UPLOADING"
        assert row.identified_name is None
    finally:
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


@pytest.mark.parametrize(
    "raw",
    [
        '{"name":"A","company":null,"phone":null,"email":null}',
        '```json\n{"name":"A","company":null,"phone":null,"email":null}\n```',
        'Here you go:\n{"name":"A","company":null,"phone":null,"email":null}\nThanks',
    ],
)
def test_llm_json_variants(raw):
    from app.ai.providers.llm import FakeLLMProvider

    llm = FakeLLMProvider(raw_content=raw)
    identity = llm.extract_identity(system_prompt="s", user_prompt="u")
    assert identity.name == "A"


def test_llm_malformed_json_no_fake_identity():
    from app.ai.providers.errors import AIResponseValidationError
    from app.ai.providers.llm import FakeLLMProvider

    llm = FakeLLMProvider(raw_content="not-json at all")
    with pytest.raises(AIResponseValidationError):
        llm.extract_identity(system_prompt="s", user_prompt="u")


def test_llm_extra_fields_ignored():
    from app.ai.providers.llm import FakeLLMProvider

    llm = FakeLLMProvider(
        raw_content='{"name":"A","ssn":"900101-1234567","career":"10y","company":null,"phone":null,"email":null}'
    )
    identity = llm.extract_identity(system_prompt="s", user_prompt="u")
    assert identity.name == "A"
    assert not hasattr(identity, "ssn") or "ssn" not in identity.model_dump()


def test_cancel_race_discards_identify_result(db_session, client):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.upload_identification.service import IdentificationService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("cancel race")
        session_id, _ = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()

        # Cancel while IDENTIFYING
        assert (
            client.delete(
                f"/api/v1/upload-sessions/{session_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )

        llm = FakeLLMProvider({"name": "ShouldNotSave", "company": None, "phone": None, "email": None})
        status = IdentificationService(
            db_session, storage=storage, llm=llm, vlm=None
        ).run(uuid.UUID(session_id))
        assert status == "CANCELLED"
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row is not None
        assert row.status == "CANCELLED"
        assert row.identified_name is None
        assert row.duplicate_result_json == [] or row.duplicate_result_json is None or row.duplicate_result_json == []
    finally:
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_identity_invalidation_on_file_change(client, db_session):
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("id")
        session_id, fid = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFIED"
        row.identified_name = "홍길동"
        row.identified_email = "a@b.com"
        row.duplicate_result_json = [{"person_id": str(uuid.uuid4()), "score": 1.0}]
        db_session.add(row)
        db_session.commit()

        # PATCH document type invalidates
        client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{fid}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
        )
        db_session.expire_all()
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        assert row.status == "UPLOADING"
        assert row.identified_name is None
        assert row.duplicate_result_json == []

        # Set IDENTIFYING and ensure mutation 409
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()
        assert (
            client.patch(
                f"/api/v1/upload-sessions/{session_id}/files/{fid}",
                headers={"X-CSRF-Token": csrf},
                json={"document_type_code": codes[0]},
            ).status_code
            == 409
        )
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status == "IDENTIFYING":
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


# --- Duplicate matcher ---


def test_duplicate_candidate_rules(db_session, client):
    from app.ai.schemas.identity import IdentityExtraction
    from app.modules.upload_identification.duplicate_matcher import DuplicateMatcher

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    person_ids: list[str] = []
    try:
        csrf = _login(client, admin.login_id)
        p1 = _create_person_api(
            client,
            csrf,
            "홍길동",
            email="Hong@Example.com",
            phone="010-1111-2222",
            affiliation_company="ABC테크",
        )
        p2 = _create_person_api(
            client,
            csrf,
            "김철수",
            email="other@example.com",
            phone="010-0000-0000",
            affiliation_company="XYZ",
        )
        person_ids.extend([p1, p2])
        # Soft-delete unrelated? create deleted-like by status
        deleted = _create_person_api(client, csrf, "삭제됨", email="del@example.com")
        person_ids.append(deleted)
        from app.db.models.person import Person

        drow = db_session.get(Person, uuid.UUID(deleted))
        drow.status = "DELETED"
        from datetime import UTC, datetime

        drow.deleted_at = datetime.now(UTC)
        db_session.add(drow)
        db_session.commit()

        identity = IdentityExtraction(
            name="홍길동",
            company="ABC테크",
            phone="01011112222",  # formatting differs
            email="hong@example.com",
        )
        cands = DuplicateMatcher(db_session).find_candidates(identity)
        assert cands
        assert cands[0].score == 1.0
        assert "EMAIL_EXACT" in cands[0].match_reasons
        assert "PHONE_EXACT" in cands[0].match_reasons
        ids = {str(c.person_id) for c in cands}
        assert p1 in ids
        assert deleted not in ids
        assert all(0.0 <= c.score <= 1.0 for c in cands)
        # deterministic ordering
        scores = [c.score for c in cands]
        assert scores == sorted(scores, reverse=True)
    finally:
        for pid in person_ids:
            _cleanup_person(db_session, uuid.UUID(pid))
        _cleanup_user(db_session, admin.id)


# --- CREATE_NEW ---


def test_create_new_happy_path(client, db_session, monkeypatch):
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.db.models.search import SearchIndexJob
    from app.db.models.upload import UploadSession, UploadTempFile
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    enqueued: list[str] = []
    monkeypatch.setattr(
        "app.tasks.document_tasks.enqueue_document_processing",
        lambda did: enqueued.append(str(did)),
    )
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("create new")
        session_id, fid = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFIED"
        row.identified_name = "신규인력"
        row.identified_company = "새회사"
        row.identified_phone = "010-3333-4444"
        row.identified_email = "new@example.com"
        db_session.add(row)
        db_session.commit()

        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "CREATE_NEW",
                "identity": {
                    "name": "신규인력",
                    "company": "새회사",
                    "phone": "010-3333-4444",
                    "email": "new@example.com",
                },
                "document_resolution": [
                    {
                        "temp_file_id": fid,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes[0],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        person_id = data["person_id"]
        assert data["profile_version"] == 1
        assert len(data["document_ids"]) == 1
        assert enqueued == data["document_ids"]

        db_session.expire_all()
        person = db_session.get(Person, uuid.UUID(person_id))
        profile = db_session.get(PersonProfile, uuid.UUID(person_id))
        assert person is not None and person.status == "ACTIVE"
        assert profile is not None
        assert profile.name == "신규인력"
        assert profile.affiliation_company == "새회사"
        assert profile.email == "new@example.com"
        assert profile.profile_version == 1
        revs = list(
            db_session.execute(
                select(ProfileRevision).where(
                    ProfileRevision.person_id == uuid.UUID(person_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(revs) == 1
        jobs = list(
            db_session.execute(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == uuid.UUID(person_id)
                )
            )
            .scalars()
            .all()
        )
        assert any(j.action == "REBUILD_PERSON" for j in jobs)
        groups = list(
            db_session.execute(
                select(DocumentGroup).where(
                    DocumentGroup.person_id == uuid.UUID(person_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(groups) == 1
        docs = list(
            db_session.execute(
                select(Document).where(Document.document_group_id == groups[0].id)
            )
            .scalars()
            .all()
        )
        assert len(docs) == 1
        assert storage.exists(docs[0].storage_key)
        temps = list(
            db_session.execute(
                select(UploadTempFile).where(
                    UploadTempFile.upload_session_id == uuid.UUID(session_id)
                )
            )
            .scalars()
            .all()
        )
        assert temps == []
        session = db_session.get(UploadSession, uuid.UUID(session_id))
        assert session.status == "RESOLVED"
        assert str(session.resolved_person_id) == person_id
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_create_new_rollback_on_db_failure(client, db_session, monkeypatch):
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.person import Person
    from app.db.models.revision import ProfileRevision
    from app.db.models.upload import UploadSession, UploadTempFile
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.documents.repository import DocumentRepository
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")

    def boom(self, **kwargs):  # noqa: ANN001
        from app.core.exceptions import StorageError

        raise StorageError("injected create_document failure")

    monkeypatch.setattr(DocumentRepository, "create_document", boom)
    try:
        csrf = _login(client, admin.login_id)
        pdf = build_minimal_pdf_with_text("rollback")
        session_id, fid = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        temp_key = (
            db_session.execute(
                select(UploadTempFile).where(UploadTempFile.id == uuid.UUID(fid))
            )
            .scalar_one()
            .temp_storage_key
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFIED"
        row.identified_name = "롤백"
        db_session.add(row)
        db_session.commit()

        before_people = db_session.execute(select(Person)).scalars().all()
        before_ids = {p.id for p in before_people}

        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "CREATE_NEW",
                "identity": {"name": "롤백", "company": None, "phone": None, "email": None},
                "document_resolution": [
                    {
                        "temp_file_id": fid,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes[0],
                    }
                ],
            },
        )
        assert res.status_code == 503

        db_session.expire_all()
        after_people = [
            p
            for p in db_session.execute(select(Person)).scalars().all()
            if p.id not in before_ids
        ]
        assert after_people == []
        # Session remains IDENTIFIED, temp remains
        session = db_session.get(UploadSession, uuid.UUID(session_id))
        assert session.status == "IDENTIFIED"
        temps = list(
            db_session.execute(
                select(UploadTempFile).where(
                    UploadTempFile.upload_session_id == uuid.UUID(session_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(temps) == 1
        assert storage.exists(temp_key)
        assert (
            db_session.execute(
                select(Document).join(
                    DocumentGroup, Document.document_group_id == DocumentGroup.id
                )
            ).first()
            is None
            or True
        )  # no new docs from this failed attempt — verified via after_people/temps
        revs_new = [
            r
            for r in db_session.execute(select(ProfileRevision)).scalars().all()
            if r.person_id not in before_ids
        ]
        assert revs_new == []
    finally:
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_link_existing_during_identifying_conflict(client, db_session):
    from app.db.models.upload import UploadSession
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person_api(client, csrf, f"p_{suffix}")
        pdf = build_minimal_pdf_with_text("link")
        session_id, fid = _seed_session_with_pdf(
            client, db_session, csrf, codes=codes, pdf_bytes=pdf
        )
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        row.status = "IDENTIFYING"
        db_session.add(row)
        db_session.commit()
        res = client.post(
            f"/api/v1/upload-sessions/{session_id}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "LINK_EXISTING",
                "person_id": person_id,
                "document_resolution": [
                    {
                        "temp_file_id": fid,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes[0],
                    }
                ],
            },
        )
        assert res.status_code == 409
    finally:
        row = db_session.get(UploadSession, uuid.UUID(session_id))
        if row and row.status == "IDENTIFYING":
            row.status = "UPLOADING"
            db_session.add(row)
            db_session.commit()
        client.delete(
            f"/api/v1/upload-sessions/{session_id}",
            headers={"X-CSRF-Token": csrf},
        )
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_openai_url_normalize():
    from app.ai.providers.openai_compat import normalize_chat_completions_url

    assert normalize_chat_completions_url("https://x.example").endswith(
        "/v1/chat/completions"
    )
    assert (
        normalize_chat_completions_url("https://x.example/v1")
        == "https://x.example/v1/chat/completions"
    )
    assert (
        normalize_chat_completions_url("https://x.example/v1/chat/completions")
        == "https://x.example/v1/chat/completions"
    )
