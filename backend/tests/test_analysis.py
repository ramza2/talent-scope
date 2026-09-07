"""Tests for Detailed AI Profile Analysis (no confirm)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

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
        "app.tasks.analysis_tasks.run_profile_analysis.delay",
        lambda *_a, **_k: None,
    )
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


def _login(client: TestClient, login_id: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})
    assert resp.status_code == 200, resp.text
    csrf = resp.json()["data"]["csrf_token"]
    return csrf


def _ensure_doc_type(db_session) -> str:
    from app.db.models.code import CodeMaster

    code = "DOC-RESUME"
    row = db_session.get(CodeMaster, code)
    if row is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type="DOC_TYPE",
                name="이력서",
                sort_order=1,
                is_active=True,
            )
        )
        db_session.commit()
    return code


def _seed_person_with_ready_doc(db_session, user_id):
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    doc_type = _ensure_doc_type(db_session)
    person = Person(status="ACTIVE", created_by=user_id)
    db_session.add(person)
    db_session.flush()
    profile = PersonProfile(
        person_id=person.id,
        name="분석대상",
        technical_grade="ADVANCED",
        profile_version=1,
    )
    db_session.add(profile)
    db_session.flush()
    snap = build_confirmed_profile_snapshot(db_session, person.id)
    db_session.add(
        ProfileRevision(
            person_id=person.id,
            revision_no=1,
            snapshot_json=snap,
            source_type="USER",
            created_by=user_id,
        )
    )
    group = DocumentGroup(
        person_id=person.id,
        document_type_code=doc_type,
        title="이력서",
    )
    db_session.add(group)
    db_session.flush()
    document = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="resume.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256="a" * 64,
        processing_status="READY",
        uploaded_by=user_id,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentPage(
            document_id=document.id,
            page_no=1,
            extracted_text="홍길동 / Python / AI 개발자 / 기술등급 특급",
            layout_json={"needs_vlm": False},
            extraction_method="TEXT_PARSER",
        )
    )
    db_session.commit()
    return person, document


def _cleanup_person(db_session, person_id, user_id) -> None:
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.user import AppUser

    run_ids = list(
        db_session.execute(
            select(AnalysisRun.id).where(AnalysisRun.person_id == person_id)
        ).scalars().all()
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
        ).scalars().all()
    )
    if group_ids:
        doc_ids = list(
            db_session.execute(
                select(Document.id).where(Document.document_group_id.in_(group_ids))
            ).scalars().all()
        )
        if doc_ids:
            db_session.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids)))
            db_session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(group_ids)))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def test_normalize_and_diff_unit():
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.diff_engine import build_diffs
    from app.modules.analysis.normalize import normalize_candidate

    catalog = {
        "JOB-AI-DEV": ("JOB", True),
        "TECH-LANG-PYTHON": ("TECH", True),
        "EXP-AI-RAG": ("EXP", True),
    }
    allowed = {"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {1}}
    raw = {
        "schema_version": "x",
        "profile": {"name": "홍길동", "technical_grade": "특급", "ssn": "1"},
        "jobs": [{"code": "JOB-AI-DEV", "job_type": "PRIMARY"}],
        "skills": [{"code": "EXP-AI-RAG", "raw_value": "RAG"}],
        "expertise": [{"code": "EXP-AI-RAG"}],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(raw, catalog=catalog, allowed_documents=allowed)
    assert doc.skills[0].code is None
    assert doc.expertise[0].code == "EXP-AI-RAG"
    assert doc.profile.technical_grade == "EXPERT"

    base = {
        "profile": {"name": "홍길동", "technical_grade": "ADVANCED"},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
    }
    specs = build_diffs(doc, base)
    assert any(s.change_type == "CONFLICT" and s.field_name == "technical_grade" for s in specs)
    assert any(s.entity_type == "JOB" and s.change_type == "NEW" for s in specs)
    assert any(s.entity_type == "TECH" and s.change_type == "REVIEW" for s in specs)


def test_create_run_list_review_retry_confirm(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    login_id = f"an_{uuid.uuid4().hex[:10]}"
    password = "Passw0rd!"
    user = _create_user(db_session, login_id=login_id, password=password)
    person, document = _seed_person_with_ready_doc(db_session, user.id)
    csrf = _login(client, login_id, password)

    # Create analysis (enqueue mocked)
    resp = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert resp.status_code == 202, resp.text
    analysis_id = resp.json()["data"]["analysis_id"]
    assert resp.json()["data"]["status"] == "QUEUED"

    profile_json = {
        "schema_version": "profile-candidate-v1",
        "profile": {
            "name": "분석대상",
            "technical_grade": "EXPERT",
            "phone": "010-9999-8888",
        },
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {"text": "요약"},
        "analysis": {"overall_confidence": 0.91},
    }
    fake_llm = FakeLLMProvider(profile_json=profile_json)

    service = AnalysisService(db_session, storage=get_object_storage(), llm=fake_llm)
    status = service.run_analysis(uuid.UUID(analysis_id), llm=fake_llm)
    assert status == "REVIEWING"

    detail = client.get(f"/api/v1/analyses/{analysis_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["status"] == "REVIEWING"
    assert body["counts"]["conflict"] >= 1 or body["counts"]["new"] >= 1
    assert body["candidate_json"]["profile"]["name"] == "분석대상"

    diffs = client.get(f"/api/v1/analyses/{analysis_id}/diffs?review_status=PENDING")
    assert diffs.status_code == 200, diffs.text
    diff_rows = diffs.json()["data"]
    assert len(diff_rows) >= 1

    conflict_or_new = next(
        d for d in diff_rows if d["change_type"] in {"CONFLICT", "NEW", "UPDATE"}
    )
    # Accept a non-CONFLICT/REVIEW via bulk when possible; single review for CONFLICT.
    if conflict_or_new["change_type"] == "CONFLICT":
        patch = client.patch(
            f"/api/v1/analyses/{analysis_id}/diffs/{conflict_or_new['id']}",
            headers={"X-CSRF-Token": csrf},
            json={"review_status": "ACCEPTED"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["data"]["review_status"] == "ACCEPTED"
    else:
        bulk = client.post(
            f"/api/v1/analyses/{analysis_id}/diffs/bulk",
            headers={"X-CSRF-Token": csrf},
            json={"diff_ids": [conflict_or_new["id"]], "review_status": "ACCEPTED"},
        )
        assert bulk.status_code == 200, bulk.text

    # Bulk reject CONFLICT
    conflict = next((d for d in diff_rows if d["change_type"] == "CONFLICT"), None)
    if conflict and conflict["id"] != conflict_or_new["id"]:
        bad = client.post(
            f"/api/v1/analyses/{analysis_id}/diffs/bulk",
            headers={"X-CSRF-Token": csrf},
            json={"diff_ids": [conflict["id"]], "review_status": "ACCEPTED"},
        )
        assert bad.status_code == 400

    # Confirm not implemented
    confirm = client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={"expected_profile_version": 1},
    )
    assert confirm.status_code == 501

    # Force FAILED then retry
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "FAILED"
    run.error_message = "boom"
    db_session.add(run)
    db_session.commit()

    retry = client.post(
        f"/api/v1/analyses/{analysis_id}/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["data"]["status"] == "QUEUED"
    remaining = db_session.execute(
        select(AnalysisDiffItem).where(
            AnalysisDiffItem.analysis_run_id == uuid.UUID(analysis_id)
        )
    ).scalars().all()
    assert remaining == []

    listing = client.get("/api/v1/analyses?status=QUEUED")
    assert listing.status_code == 200
    assert any(i["analysis_id"] == analysis_id for i in listing.json()["data"])

    _cleanup_person(db_session, person.id, user.id)


def test_create_rejects_non_ready_document(client: TestClient, db_session):
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision

    login_id = f"an2_{uuid.uuid4().hex[:10]}"
    password = "Passw0rd!"
    user = _create_user(db_session, login_id=login_id, password=password)
    csrf = _login(client, login_id, password)
    doc_type = _ensure_doc_type(db_session)

    person = Person(status="ACTIVE", created_by=user.id)
    db_session.add(person)
    db_session.flush()
    db_session.add(PersonProfile(person_id=person.id, name="X", profile_version=1))
    db_session.add(
        ProfileRevision(
            person_id=person.id,
            revision_no=1,
            snapshot_json={"profile": {"name": "X"}},
            source_type="USER",
            created_by=user.id,
        )
    )
    group = DocumentGroup(person_id=person.id, document_type_code=doc_type, title="t")
    db_session.add(group)
    db_session.flush()
    document = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="pending.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=10,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256="b" * 64,
        processing_status="PROCESSING",
        uploaded_by=user.id,
    )
    db_session.add(document)
    db_session.commit()

    resp = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert resp.status_code == 400

    _cleanup_person(db_session, person.id, user.id)
