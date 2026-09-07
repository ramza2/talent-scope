"""Document processing worker tests — MemoryObjectStorage + mocked LibreOffice."""

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

    # Never hit a real Celery broker from resolve enqueue during API tests.
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


def _create_person(client: TestClient, csrf: str, name: str) -> str:
    response = client.post(
        "/api/v1/people",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
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


def _seed_document(
    db_session,
    storage,
    *,
    person_id: uuid.UUID,
    user_id: uuid.UUID,
    code: str,
    filename: str,
    extension: str,
    data: bytes,
    mime: str,
):
    from app.db.models.document import Document, DocumentGroup

    group = DocumentGroup(
        person_id=person_id,
        document_type_code=code,
        title=filename,
    )
    db_session.add(group)
    db_session.flush()
    doc_id = uuid.uuid4()
    key = f"documents/{person_id}/{group.id}/{doc_id}/original"
    storage.put_bytes(key, data, content_type=mime)
    doc = Document(
        id=doc_id,
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename=filename,
        extension=extension,
        mime_type=mime,
        file_size=len(data),
        storage_key=key,
        sha256="a" * 64,
        processing_status="UPLOADED",
        uploaded_by=user_id,
    )
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc, group


def test_pdf_processing_ready_and_pages(db_session, client):
    from app.db.models.document import Document, DocumentPage
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"proc_{suffix}"))
    try:
        pdf = build_minimal_pdf_with_text("Hello TalentScope PDF")
        doc, _group = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="resume.pdf",
            extension="pdf",
            data=pdf,
            mime="application/pdf",
        )
        service = DocumentProcessingService(db_session, storage=storage)
        status = service.process_document(doc.id)
        assert status == "READY"
        db_session.expire_all()
        row = db_session.get(Document, doc.id)
        assert row is not None
        assert row.processing_status == "READY"
        assert row.preview_storage_key is None  # native PDF uses original
        assert row.preview_page_count == 1
        pages = list(
            db_session.execute(
                select(DocumentPage).where(DocumentPage.document_id == doc.id)
            )
            .scalars()
            .all()
        )
        assert len(pages) == 1
        assert pages[0].page_no == 1
        assert pages[0].extraction_method == "TEXT_PARSER"
        assert pages[0].extracted_text and "TalentScope" in pages[0].extracted_text

        # Idempotent re-run
        status2 = service.process_document(doc.id)
        assert status2 == "READY"
        pages2 = list(
            db_session.execute(
                select(DocumentPage).where(DocumentPage.document_id == doc.id)
            )
            .scalars()
            .all()
        )
        assert len(pages2) == 1
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_scanned_pdf_needs_vlm(db_session, client):
    from app.db.models.document import DocumentPage
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache
    import pymupdf

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"scan_{suffix}"))
    try:
        # Empty page PDF → little/no text
        empty = pymupdf.open()
        empty.new_page()
        pdf = empty.tobytes()
        empty.close()
        doc, _ = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="scan.pdf",
            extension="pdf",
            data=pdf,
            mime="application/pdf",
        )
        status = DocumentProcessingService(db_session, storage=storage).process_document(
            doc.id
        )
        assert status == "READY"
        page = db_session.execute(
            select(DocumentPage).where(DocumentPage.document_id == doc.id)
        ).scalar_one()
        assert page.layout_json and page.layout_json.get("needs_vlm") is True
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_image_needs_vlm(db_session, client):
    from app.db.models.document import DocumentPage
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-CERT-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "자격")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"img_{suffix}"))
    try:
        # Minimal PNG signature bytes are enough for this stage (no decode).
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 32
        )
        doc, _ = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="cert.png",
            extension="png",
            data=png,
            mime="image/png",
        )
        status = DocumentProcessingService(db_session, storage=storage).process_document(
            doc.id
        )
        assert status == "READY"
        page = db_session.execute(
            select(DocumentPage).where(DocumentPage.document_id == doc.id)
        ).scalar_one()
        assert page.page_no == 1
        assert page.layout_json and page.layout_json.get("needs_vlm") is True
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_office_converter_success_sets_preview(db_session, client):
    from app.db.models.document import Document
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"docx_{suffix}"))
    try:
        fake_pdf = build_minimal_pdf_with_text("From DOCX")
        converter = FakeConverter(fake_pdf)
        doc, group = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="resume.docx",
            extension="docx",
            data=b"PK-fake-docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        status = DocumentProcessingService(
            db_session, storage=storage, converter=converter
        ).process_document(doc.id)
        assert status == "READY"
        assert converter.calls == 1
        db_session.expire_all()
        row = db_session.get(Document, doc.id)
        assert row is not None
        expected_key = f"documents/{person_id}/{group.id}/{doc.id}/preview.pdf"
        assert row.preview_storage_key == expected_key
        assert storage.exists(expected_key)
        assert row.preview_page_count == 1
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_converter_failure_marks_failed_keeps_original(db_session, client):
    from app.db.models.document import Document, DocumentPage
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"fail_{suffix}"))
    try:
        converter = FakeConverter(b"%PDF-x", fail=True)
        doc, _ = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="bad.docx",
            extension="docx",
            data=b"PK-bad",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        original_key = doc.storage_key
        status = DocumentProcessingService(
            db_session, storage=storage, converter=converter
        ).process_document(doc.id)
        assert status == "FAILED"
        db_session.expire_all()
        row = db_session.get(Document, doc.id)
        assert row is not None
        assert row.processing_status == "FAILED"
        assert row.processing_error
        assert storage.exists(original_key)
        pages = list(
            db_session.execute(
                select(DocumentPage).where(DocumentPage.document_id == doc.id)
            )
            .scalars()
            .all()
        )
        assert pages == []
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_preview_upload_db_failure_compensates(db_session, client, monkeypatch):
    from app.db.models.document import Document
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.document_processing.repository import DocumentProcessingRepository
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"comp_{suffix}"))
    try:
        fake_pdf = build_minimal_pdf_with_text("compensate")
        converter = FakeConverter(fake_pdf)
        doc, group = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="x.docx",
            extension="docx",
            data=b"PK-x",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        preview_key = f"documents/{person_id}/{group.id}/{doc.id}/preview.pdf"

        def boom(self, document_id, pages):  # noqa: ANN001
            raise RuntimeError("injected replace_pages failure")

        monkeypatch.setattr(DocumentProcessingRepository, "replace_pages", boom)
        status = DocumentProcessingService(
            db_session, storage=storage, converter=converter
        ).process_document(doc.id)
        assert status == "FAILED"
        assert not storage.exists(preview_key)
        db_session.expire_all()
        row = db_session.get(Document, doc.id)
        assert row is not None
        assert row.processing_status == "FAILED"
        assert storage.exists(doc.storage_key)
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()


def test_resolve_enqueue_and_enqueue_failure_keeps_uploaded(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.document import Document
    from app.db.models.upload import UploadSession, UploadTempFile
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text

    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    person_id = None
    session_id = None
    enqueued: list[str] = []

    def capture(doc_id):
        enqueued.append(str(doc_id))

    monkeypatch.setattr(
        "app.tasks.document_tasks.enqueue_document_processing", capture
    )
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"enq_{suffix}")
        session_id = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        pdf = build_minimal_pdf_with_text("enqueue me")
        up = client.post(
            f"/api/v1/upload-sessions/{session_id}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("a.pdf", io.BytesIO(pdf), "application/pdf"))],
        )
        fid = up.json()["data"][0]["temp_file_id"]
        client.patch(
            f"/api/v1/upload-sessions/{session_id}/files/{fid}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes[0]},
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
        doc_id = res.json()["data"]["document_ids"][0]
        assert enqueued == [doc_id]
        db_session.expire_all()
        doc = db_session.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        assert doc.processing_status == "UPLOADED"
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        if session_id:
            db_session.execute(
                delete(UploadTempFile).where(
                    UploadTempFile.upload_session_id == uuid.UUID(session_id)
                )
            )
            db_session.execute(
                delete(UploadSession).where(UploadSession.id == uuid.UUID(session_id))
            )
            db_session.commit()
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)

    # Broker failure during enqueue must not undo resolve.
    suffix2 = uuid.uuid4().hex[:8]
    codes2 = [f"DOC-RESUME-{suffix2}"]
    _ensure_code(db_session, codes2[0], "DOC_TYPE", "이력서")
    admin2 = _create_user(db_session, login_id=f"b_{suffix2}", password="Secret123!")
    person_id2 = None
    session_id2 = None
    monkeypatch.setattr(
        "app.tasks.document_tasks.process_document.delay",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("broker down")),
    )
    try:
        csrf = _login(client, admin2.login_id)
        person_id2 = _create_person(client, csrf, f"enqfail_{suffix2}")
        session_id2 = client.post(
            "/api/v1/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={},
        ).json()["data"]["id"]
        pdf = build_minimal_pdf_with_text("still uploaded")
        up = client.post(
            f"/api/v1/upload-sessions/{session_id2}/files",
            headers={"X-CSRF-Token": csrf},
            files=[("files", ("b.pdf", io.BytesIO(pdf), "application/pdf"))],
        )
        fid = up.json()["data"][0]["temp_file_id"]
        client.patch(
            f"/api/v1/upload-sessions/{session_id2}/files/{fid}",
            headers={"X-CSRF-Token": csrf},
            json={"document_type_code": codes2[0]},
        )
        res = client.post(
            f"/api/v1/upload-sessions/{session_id2}/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "mode": "LINK_EXISTING",
                "person_id": person_id2,
                "document_resolution": [
                    {
                        "temp_file_id": fid,
                        "mode": "NEW_GROUP",
                        "document_type_code": codes2[0],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        doc_id = res.json()["data"]["document_ids"][0]
        db_session.expire_all()
        doc = db_session.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        assert doc.processing_status == "UPLOADED"
    finally:
        if person_id2:
            _cleanup_person(db_session, uuid.UUID(person_id2))
        if session_id2:
            db_session.execute(
                delete(UploadTempFile).where(
                    UploadTempFile.upload_session_id == uuid.UUID(session_id2)
                )
            )
            db_session.execute(
                delete(UploadSession).where(UploadSession.id == uuid.UUID(session_id2))
            )
            db_session.commit()
        _cleanup_codes(db_session, codes2)
        _cleanup_user(db_session, admin2.id)


def test_replace_pages_does_not_touch_other_documents(db_session, client):
    from app.db.models.document import DocumentPage
    from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
    from app.modules.document_processing.service import DocumentProcessingService
    from app.storage.s3 import build_object_storage, reset_object_storage_cache

    reset_object_storage_cache()
    storage = build_object_storage()
    suffix = uuid.uuid4().hex[:8]
    codes = [f"DOC-RESUME-{suffix}"]
    _ensure_code(db_session, codes[0], "DOC_TYPE", "이력서")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!")
    csrf = _login(client, admin.login_id)
    person_id = uuid.UUID(_create_person(client, csrf, f"iso_{suffix}"))
    try:
        pdf = build_minimal_pdf_with_text("doc-a")
        doc_a, _ = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="a.pdf",
            extension="pdf",
            data=pdf,
            mime="application/pdf",
        )
        doc_b, _ = _seed_document(
            db_session,
            storage,
            person_id=person_id,
            user_id=admin.id,
            code=codes[0],
            filename="b.pdf",
            extension="pdf",
            data=build_minimal_pdf_with_text("doc-b"),
            mime="application/pdf",
        )
        svc = DocumentProcessingService(db_session, storage=storage)
        assert svc.process_document(doc_a.id) == "READY"
        assert svc.process_document(doc_b.id) == "READY"
        assert svc.process_document(doc_a.id) == "READY"  # re-run A
        pages_b = list(
            db_session.execute(
                select(DocumentPage).where(DocumentPage.document_id == doc_b.id)
            )
            .scalars()
            .all()
        )
        assert len(pages_b) == 1
        assert pages_b[0].extracted_text and "doc-b" in pages_b[0].extracted_text
    finally:
        _cleanup_person(db_session, person_id)
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)
        reset_object_storage_cache()
