"""Tests for Detailed AI Profile Analysis (no confirm)."""

from __future__ import annotations

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
    csrf = client.cookies.get("ts_csrf")
    assert csrf
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


def test_user_forbidden_and_admin_csrf(client: TestClient, db_session):
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision

    user = _create_user(
        db_session, login_id=f"u_{uuid.uuid4().hex[:10]}", password="Passw0rd!", role="USER"
    )
    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!", role="ADMIN"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    payload = {
        "person_id": str(person.id),
        "document_ids": [str(document.id)],
        "analysis_type": "PROFILE",
    }

    _login(client, user.login_id, "Passw0rd!")
    forbidden = client.post("/api/v1/analyses", json=payload)
    assert forbidden.status_code == 403

    # Re-login as admin; miss CSRF on mutating endpoints
    csrf = _login(client, admin.login_id, "Passw0rd!")
    no_csrf = client.post("/api/v1/analyses", json=payload)
    assert no_csrf.status_code == 403

    ok = client.post("/api/v1/analyses", headers={"X-CSRF-Token": csrf}, json=payload)
    assert ok.status_code == 202, ok.text
    analysis_id = ok.json()["data"]["analysis_id"]

    # Force REVIEWING for review CSRF checks
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "REVIEWING"
    db_session.add(run)
    diff = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.phone",
        field_name="phone",
        change_type="NEW",
        new_value="010",
        review_status="PENDING",
    )
    db_session.add(diff)
    db_session.commit()
    db_session.refresh(diff)

    assert (
        client.patch(
            f"/api/v1/analyses/{analysis_id}/diffs/{diff.id}",
            json={"review_status": "ACCEPTED"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/analyses/{analysis_id}/diffs/bulk",
            json={"diff_ids": [str(diff.id)], "review_status": "ACCEPTED"},
        ).status_code
        == 403
    )

    # USER cannot list
    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    _login(client, user.login_id, "Passw0rd!")
    assert client.get("/api/v1/analyses").status_code == 403

    _cleanup_person(db_session, person.id, admin.id)
    from app.db.models.user import AppUser
    from app.db.models.revision import AuditLog

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user.id))
    db_session.execute(delete(AppUser).where(AppUser.id == user.id))
    db_session.commit()


def test_create_rejects_deleted_person_and_cross_person_doc(client: TestClient, db_session):
    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person_a, doc_a = _seed_person_with_ready_doc(db_session, admin.id)
    person_b, doc_b = _seed_person_with_ready_doc(db_session, admin.id)

    # empty documents → 422
    empty = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={"person_id": str(person_a.id), "document_ids": [], "analysis_type": "PROFILE"},
    )
    assert empty.status_code == 422

    mixed = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person_a.id),
            "document_ids": [str(doc_b.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert mixed.status_code == 400

    from app.db.models.person import Person

    person_a.status = "DELETED"
    db_session.add(person_a)
    db_session.commit()
    deleted = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person_a.id),
            "document_ids": [str(doc_a.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert deleted.status_code == 404

    person_a.status = "ACTIVE"
    db_session.add(person_a)
    db_session.commit()
    _cleanup_person(db_session, person_a.id, admin.id)
    # person_b cleanup without deleting admin twice
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import PersonProfile
    from app.db.models.revision import ProfileRevision

    pid = person_b.id
    run_ids = list(
        db_session.execute(select(AnalysisRun.id).where(AnalysisRun.person_id == pid))
        .scalars()
        .all()
    )
    if run_ids:
        db_session.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id.in_(run_ids))
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(AnalysisRunDocument.analysis_run_id.in_(run_ids))
        )
        db_session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))
    group_ids = list(
        db_session.execute(select(DocumentGroup.id).where(DocumentGroup.person_id == pid))
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
            db_session.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids)))
            db_session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(group_ids)))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == pid))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == pid))
    db_session.execute(delete(Person).where(Person.id == pid))
    db_session.commit()


def test_create_records_base_version_and_enqueue_fail(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun, AnalysisRunDocument

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)

    def boom(*_a, **_k):
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.tasks.analysis_tasks.enqueue_profile_analysis", boom)
    resp = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "AI_QUEUE_UNAVAILABLE"

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.person_id == person.id)
    ).scalar_one()
    assert run.status == "FAILED"
    assert run.base_profile_version == 1
    assert run.error_message
    docs = db_session.execute(
        select(AnalysisRunDocument).where(AnalysisRunDocument.analysis_run_id == run.id)
    ).scalars().all()
    assert len(docs) == 1
    assert docs[0].document_id == document.id

    _cleanup_person(db_session, person.id, admin.id)


def test_base_revision_snapshot_not_live_profile(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    """Diff old_value must come from base revision, not live profile after bump."""
    from app.ai.providers.llm import FakeLLMProvider
    from app.db.models.person import PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.analysis.service import AnalysisService
    from app.modules.people.snapshot import build_confirmed_profile_snapshot
    from app.storage.s3 import get_object_storage

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)

    # Bump live profile to v2 with different phone AFTER creating analysis at v1
    resp = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert resp.status_code == 202
    analysis_id = resp.json()["data"]["analysis_id"]

    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    # Ensure v1 snapshot has phone=None / known value
    rev1 = db_session.execute(
        select(ProfileRevision).where(
            ProfileRevision.person_id == person.id, ProfileRevision.revision_no == 1
        )
    ).scalar_one()
    snap1 = dict(rev1.snapshot_json)
    snap1.setdefault("profile", {})["phone"] = None
    snap1["profile"]["name"] = "분석대상"
    rev1.snapshot_json = snap1
    db_session.add(rev1)

    profile.phone = "010-1111-2222"
    profile.name = "라이브이름"
    profile.profile_version = 2
    db_session.add(profile)
    db_session.flush()
    snap2 = build_confirmed_profile_snapshot(db_session, person.id)
    db_session.add(
        ProfileRevision(
            person_id=person.id,
            revision_no=2,
            snapshot_json=snap2,
            source_type="USER",
            created_by=admin.id,
        )
    )
    db_session.commit()

    profile_json = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "분석대상", "phone": "010-9999-0000"},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {"overall_confidence": 0.8},
    }
    service = AnalysisService(
        db_session, storage=get_object_storage(), llm=FakeLLMProvider(profile_json=profile_json)
    )
    assert service.run_analysis(uuid.UUID(analysis_id)) == "REVIEWING"

    diffs = client.get(f"/api/v1/analyses/{analysis_id}/diffs").json()["data"]
    phone_diff = next(d for d in diffs if d.get("field_name") == "phone")
    # old from v1 (null), not live v2 phone
    assert phone_diff["old_value"] in (None, "")
    assert phone_diff["new_value"] in ("010-9999-0000", {"value": "010-9999-0000"}) or (
        isinstance(phone_diff["new_value"], dict)
        and phone_diff["new_value"].get("value") == "010-9999-0000"
    ) or phone_diff["new_value"] == "010-9999-0000"
    assert phone_diff["change_type"] == "NEW"

    name_same = next((d for d in diffs if d.get("field_name") == "name"), None)
    # name matches v1 snapshot "분석대상" → SAME or absent NEW conflict with live
    if name_same:
        assert name_same["old_value"] == "분석대상"
        assert name_same["change_type"] == "SAME"

    _cleanup_person(db_session, person.id, admin.id)


def test_atomic_claim_exactly_once(db_session):
    from app.modules.analysis.repository import AnalysisRepository
    from app.db.models.analysis import AnalysisRun

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, _document = _seed_person_with_ready_doc(db_session, admin.id)
    run = AnalysisRun(
        person_id=person.id,
        status="QUEUED",
        candidate_json={},
        base_profile_version=1,
        prompt_version="profile-extract-v1",
        schema_version="profile-candidate-v1",
    )
    db_session.add(run)
    db_session.commit()

    from app.db.session import SessionLocal

    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        r1 = AnalysisRepository(s1).try_claim_analysis(run.id)
        s1.commit()
        r2 = AnalysisRepository(s2).try_claim_analysis(run.id)
        s2.commit()
        assert r1 is not None
        assert r2 is None
        db_session.refresh(run)
        assert run.status == "PROCESSING"
    finally:
        s1.close()
        s2.close()
        _cleanup_person(db_session, person.id, admin.id)


def test_duplicate_worker_skips_second_ai_call(db_session, monkeypatch: pytest.MonkeyPatch):
    from app.ai.providers.llm import FakeLLMProvider
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)

    from app.modules.analysis.repository import AnalysisRepository

    repo = AnalysisRepository(db_session)
    run = repo.create_run(
        person_id=person.id,
        base_profile_version=1,
        llm_model="fake",
        vlm_model="fake",
        prompt_version="profile-extract-v1",
        schema_version="profile-candidate-v1",
    )
    repo.add_run_documents(run.id, [document.id])
    db_session.commit()

    calls = {"n": 0}
    real = FakeLLMProvider(
        profile_json={
            "schema_version": "profile-candidate-v1",
            "profile": {"name": "분석대상"},
            "jobs": [],
            "skills": [],
            "expertise": [],
            "employment_history": [],
            "education": [],
            "certifications": [],
            "projects": [],
            "summary": {},
            "analysis": {},
        }
    )

    class CountingLLM:
        def complete_json(self, **kwargs):
            calls["n"] += 1
            return real.complete_json(**kwargs)

    llm = CountingLLM()
    s1 = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert s1.run_analysis(run.id) == "REVIEWING"
    assert calls["n"] == 1

    # Second worker sees non-QUEUED
    assert s1.run_analysis(run.id) == "REVIEWING"
    assert calls["n"] == 1

    _cleanup_person(db_session, person.id, admin.id)


def test_malformed_json_and_sanitized_error(db_session):
    from app.ai.providers.errors import AIResponseValidationError
    from app.modules.analysis.service import AnalysisService
    from app.modules.analysis.repository import AnalysisRepository
    from app.storage.s3 import get_object_storage

    class BadLLM:
        def complete_json(self, **_k):
            raise AIResponseValidationError("not json at all with secret key=abc")

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    repo = AnalysisRepository(db_session)
    run = repo.create_run(
        person_id=person.id,
        base_profile_version=1,
        llm_model="fake",
        vlm_model="fake",
        prompt_version="profile-extract-v1",
        schema_version="profile-candidate-v1",
    )
    repo.add_run_documents(run.id, [document.id])
    db_session.commit()

    service = AnalysisService(db_session, storage=get_object_storage(), llm=BadLLM())
    assert service.run_analysis(run.id) == "FAILED"
    db_session.refresh(run)
    assert run.status == "FAILED"
    assert "secret" not in (run.error_message or "").lower()
    assert "abc" not in (run.error_message or "")
    assert run.error_message == "AI response validation failed"

    _cleanup_person(db_session, person.id, admin.id)


def test_candidate_source_refs_and_inactive_codes():
    from app.modules.analysis.diff_engine import build_diffs
    from app.modules.analysis.normalize import normalize_candidate

    catalog = {
        "JOB-AI-DEV": ("JOB", True),
        "TECH-LANG-PYTHON": ("TECH", True),
        "TECH-OLD": ("TECH", False),
        "EXP-AI-RAG": ("EXP", True),
        "JOB-FAKE": ("JOB", False),
    }
    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    raw = {
        "schema_version": "x",
        "profile": {"name": "홍길동", "ssn": "900101-1234567", "certificate_no": "X"},
        "jobs": [
            {
                "code": "JOB-UNKNOWN",
                "raw_value": "Unknown Job",
                "job_type": "PRIMARY",
                "source_refs": [
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "문서에있는인용",
                    },
                    {
                        "document_id": doc_id,
                        "page_no": 9,
                        "quote_text": "wrong page",
                    },
                    {
                        "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "page_no": 1,
                        "quote_text": "wrong doc",
                    },
                    {
                        "document_id": doc_id,
                        "page_no": 1,
                        "quote_text": "없는인용문환각",
                    },
                ],
            }
        ],
        "skills": [{"code": "TECH-OLD", "raw_value": "Old"}],
        "expertise": [{"code": "EXP-AI-RAG", "evidence_type": "EXPLICIT"}],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(
        raw,
        catalog=catalog,
        allowed_documents={doc_id: {1}},
        page_texts={(doc_id, 1): "문서에있는인용 과 기타"},
    )
    dumped = doc.to_storage_dict()
    assert "ssn" not in dumped.get("profile", {})
    assert "certificate_no" not in dumped.get("profile", {})
    assert doc.jobs[0].code is None
    assert doc.skills[0].code is None
    assert len(doc.jobs[0].source_refs) == 2  # invalid doc/page dropped; hallucinated quote stripped
    assert all(r.document_id == doc_id and r.page_no == 1 for r in doc.jobs[0].source_refs)
    quotes = [r.quote_text for r in doc.jobs[0].source_refs]
    assert "문서에있는인용" in quotes
    assert "없는인용문환각" not in quotes
    assert "wrong page" not in quotes
    assert "wrong doc" not in quotes

    base = {
        "profile": {"name": "홍길동"},
        "jobs": [],
        "skills": [{"code": "TECH-LANG-PYTHON"}],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [{"id": "11111111-1111-1111-1111-111111111111", "project_name": "OldProj", "customer_name": "C", "start_date": "2020", "end_date": "2021"}],
    }
    specs = build_diffs(doc, base)
    assert any(s.entity_type == "JOB" and s.change_type == "REVIEW" for s in specs)
    # existing TECH / Project not in candidate → no REMOVE
    assert not any(s.change_type == "REMOVE_CANDIDATE" for s in specs)
    assert not any(
        s.entity_type == "PROJECT" and s.change_type in {"NEW", "UPDATE", "CONFLICT", "REVIEW", "SAME"}
        and s.change_type == "REMOVE_CANDIDATE"
        for s in specs
    )
    assert not any(
        s.entity_type == "TECH" and getattr(s, "old_value", None) == {"code": "TECH-LANG-PYTHON"}
        and s.change_type not in {"SAME", "NEW", "UPDATE", "CONFLICT", "REVIEW"}
        for s in specs
    )
    # Existing-only project should not produce a remove diff
    assert not any(
        s.entity_type == "PROJECT" and "OldProj" in str(s.new_value or "")
        for s in specs
        if s.change_type != "SAME"
    )


def test_diff_matrix_entities():
    from app.ai.schemas.profile_candidate import (
        EducationCandidate,
        EmploymentCandidate,
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SkillCandidate,
        CertificationCandidate,
        AnalysisMetaCandidate,
        SummaryCandidate,
    )
    from app.modules.analysis.diff_engine import build_diffs

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(
            name="홍길동",
            technical_grade="EXPERT",
            phone="010-1",
            email=None,
        ),
        jobs=[
            JobCandidate(code="JOB-AI-DEV", job_type="PRIMARY", raw_value="AI"),
            JobCandidate(code=None, raw_value="미매핑", job_type="SECONDARY"),
        ],
        skills=[
            SkillCandidate(code="TECH-LANG-PYTHON", last_used_year=2026),
            SkillCandidate(code="TECH-LANG-GO", raw_value="Go"),
        ],
        expertise=[
            ExpertiseCandidate(code="EXP-AI-RAG", evidence_type="EXPLICIT"),
            ExpertiseCandidate(code="EXP-NEW", evidence_type="EXPLICIT", raw_value="new"),
        ],
        employment_history=[
            EmploymentCandidate(
                company_name="A사", start_date="2020", end_date="2021", title="Eng"
            ),
            EmploymentCandidate(
                company_name="B사", start_date="2022", end_date="2023", title="Lead"
            ),
        ],
        education=[
            EducationCandidate(
                school_name="K대", degree="학사", start_date="2010", end_date="2014", major="CS"
            )
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사", issuer="한국산업인력공단", acquired_date="2015"
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="same",
            ),
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2022",
                end_date="2023",
                project_summary="similar dates",
            ),
            ProjectCandidate(
                project_name="BrandNew",
                customer_name="X",
                start_date="2024",
                end_date="2025",
            ),
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    # Fix expertise NEW code — will be treated as new with code present
    base = {
        "profile": {"name": "홍길동", "technical_grade": "ADVANCED", "phone": None},
        "jobs": [{"code": "JOB-AI-DEV", "job_type": "PRIMARY"}],
        "skills": [{"code": "TECH-LANG-PYTHON", "last_used_year": 2024}],
        "expertise": [{"code": "EXP-AI-RAG", "evidence_type": "EXPLICIT"}],
        "employment_history": [
            {"company_name": "A사", "start_date": "2020", "end_date": "2021", "title": None}
        ],
        "education": [],
        "certifications": [],
        "projects": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "project_name": "P1",
                "customer_name": "C",
                "start_date": "2020",
                "end_date": "2021",
                "project_summary": "same",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    by = {(s.entity_type, s.change_type) for s in specs}
    assert ("PROFILE", "CONFLICT") in by  # technical_grade
    assert ("PROFILE", "NEW") in by  # phone
    assert ("JOB", "SAME") in by
    assert ("JOB", "REVIEW") in by
    assert ("TECH", "NEW") in by  # GO
    assert ("TECH", "UPDATE") in by  # python last_used_year metadata
    assert ("EXP", "SAME") in by
    assert ("EXP", "NEW") in by
    assert ("EMPLOYMENT", "UPDATE") in by or any(
        s.entity_type == "EMPLOYMENT" and s.change_type == "UPDATE" for s in specs
    )
    assert ("EMPLOYMENT", "NEW") in by
    assert ("EDUCATION", "NEW") in by
    assert ("CERTIFICATION", "NEW") in by
    assert ("PROJECT", "SAME") in by
    assert ("PROJECT", "REVIEW") in by  # similar name different dates
    assert ("PROJECT", "NEW") in by


def test_review_modified_merged_audit(client: TestClient, db_session):
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.project import Project
    from app.db.models.revision import AuditLog

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)

    create = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    analysis_id = create.json()["data"]["analysis_id"]
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "REVIEWING"
    db_session.add(run)

    project = Project(
        person_id=person.id,
        project_name="Existing",
        customer_name="Cust",
        start_date="2020-01-01",
        end_date="2020-12-31",
    )
    db_session.add(project)
    db_session.flush()

    d_mod = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.phone",
        field_name="phone",
        change_type="NEW",
        new_value="010",
        review_status="PENDING",
    )
    d_proj = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROJECT",
        candidate_path="projects[0]",
        change_type="REVIEW",
        new_value={"project_name": "Existing"},
        review_status="PENDING",
    )
    d_job = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={"code": "JOB-AI-DEV"},
        review_status="PENDING",
    )
    db_session.add_all([d_mod, d_proj, d_job])
    db_session.commit()
    db_session.refresh(d_mod)
    db_session.refresh(d_proj)
    db_session.refresh(d_job)

    bad_mod = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{d_mod.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "MODIFIED"},
    )
    assert bad_mod.status_code == 400

    ok_mod = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{d_mod.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "MODIFIED", "decided_value": "010-1234-5678"},
    )
    assert ok_mod.status_code == 200, ok_mod.text
    body = ok_mod.json()["data"]
    assert body["review_status"] == "MODIFIED"
    assert body["decided_value"] == "010-1234-5678"
    assert body["decided_by"] == str(admin.id)
    assert body["decided_at"]

    bad_merge = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{d_job.id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "review_status": "MERGED",
            "existing_target_id": str(project.id),
        },
    )
    assert bad_merge.status_code == 400

    other_person, _ = _seed_person_with_ready_doc(db_session, admin.id)
    other_proj = Project(
        person_id=other_person.id,
        project_name="Other",
        customer_name="O",
        start_date="2021-01-01",
        end_date="2021-12-31",
    )
    db_session.add(other_proj)
    db_session.commit()
    db_session.refresh(other_proj)

    wrong_owner = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{d_proj.id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "review_status": "MERGED",
            "existing_target_id": str(other_proj.id),
        },
    )
    assert wrong_owner.status_code == 400

    ok_merge = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{d_proj.id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "review_status": "MERGED",
            "existing_target_id": str(project.id),
            "decided_value": {"project_name": "Existing"},
        },
    )
    assert ok_merge.status_code == 200, ok_merge.text

    audits = db_session.execute(
        select(AuditLog).where(AuditLog.action_type == "ANALYSIS_DIFF_REVIEW")
    ).scalars().all()
    assert any(a.target_type == "ANALYSIS_DIFF_ITEM" for a in audits)
    assert any(
        (a.metadata_json or {}).get("person_id") == str(person.id) for a in audits
    )

    # cleanup other person projects
    from app.db.models.person import Person, PersonProfile
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.revision import ProfileRevision

    for pid in (other_person.id,):
        db_session.execute(delete(Project).where(Project.person_id == pid))
        gids = list(
            db_session.execute(select(DocumentGroup.id).where(DocumentGroup.person_id == pid))
            .scalars()
            .all()
        )
        if gids:
            dids = list(
                db_session.execute(
                    select(Document.id).where(Document.document_group_id.in_(gids))
                )
                .scalars()
                .all()
            )
            if dids:
                db_session.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(dids)))
                db_session.execute(delete(Document).where(Document.id.in_(dids)))
            db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(gids)))
        db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == pid))
        db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == pid))
        db_session.execute(delete(Person).where(Person.id == pid))
    db_session.execute(delete(Project).where(Project.person_id == person.id))
    db_session.commit()
    _cleanup_person(db_session, person.id, admin.id)


def test_bulk_conflict_reject_and_foreign_diff(client: TestClient, db_session):
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    person2, document2 = _seed_person_with_ready_doc(db_session, admin.id)

    a1 = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    ).json()["data"]["analysis_id"]
    a2 = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person2.id),
            "document_ids": [str(document2.id)],
            "analysis_type": "PROFILE",
        },
    ).json()["data"]["analysis_id"]

    for aid in (a1, a2):
        run = db_session.execute(
            select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(aid))
        ).scalar_one()
        run.status = "REVIEWING"
        db_session.add(run)

    d_new = AnalysisDiffItem(
        analysis_run_id=uuid.UUID(a1),
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={"code": "X"},
        review_status="PENDING",
    )
    d_conflict = AnalysisDiffItem(
        analysis_run_id=uuid.UUID(a1),
        entity_type="PROFILE",
        candidate_path="profile.name",
        field_name="name",
        change_type="CONFLICT",
        old_value="A",
        new_value="B",
        review_status="PENDING",
    )
    d_foreign = AnalysisDiffItem(
        analysis_run_id=uuid.UUID(a2),
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={"code": "Y"},
        review_status="PENDING",
    )
    db_session.add_all([d_new, d_conflict, d_foreign])
    db_session.commit()
    db_session.refresh(d_new)
    db_session.refresh(d_conflict)
    db_session.refresh(d_foreign)

    accept_new = client.post(
        f"/api/v1/analyses/{a1}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(d_new.id)], "review_status": "ACCEPTED"},
    )
    assert accept_new.status_code == 200
    assert accept_new.json()["data"][0]["review_status"] == "ACCEPTED"

    # only selected changed — conflict still PENDING
    db_session.refresh(d_conflict)
    assert d_conflict.review_status == "PENDING"

    bad_conflict = client.post(
        f"/api/v1/analyses/{a1}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(d_conflict.id)], "review_status": "ACCEPTED"},
    )
    assert bad_conflict.status_code == 400

    # bulk REJECT of CONFLICT allowed
    reject_conflict = client.post(
        f"/api/v1/analyses/{a1}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(d_conflict.id)], "review_status": "REJECTED"},
    )
    assert reject_conflict.status_code == 200

    foreign = client.post(
        f"/api/v1/analyses/{a1}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(d_foreign.id)], "review_status": "ACCEPTED"},
    )
    assert foreign.status_code == 404

    _cleanup_person(db_session, person.id, admin.id)
    # cleanup person2 without wiping admin
    from app.db.models.person import Person, PersonProfile
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.revision import ProfileRevision
    from app.db.models.analysis import AnalysisRunDocument

    pid = person2.id
    run_ids = list(
        db_session.execute(select(AnalysisRun.id).where(AnalysisRun.person_id == pid))
        .scalars()
        .all()
    )
    if run_ids:
        db_session.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id.in_(run_ids))
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(AnalysisRunDocument.analysis_run_id.in_(run_ids))
        )
        db_session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))
    gids = list(
        db_session.execute(select(DocumentGroup.id).where(DocumentGroup.person_id == pid))
        .scalars()
        .all()
    )
    if gids:
        dids = list(
            db_session.execute(select(Document.id).where(Document.document_group_id.in_(gids)))
            .scalars()
            .all()
        )
        if dids:
            db_session.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(dids)))
            db_session.execute(delete(Document).where(Document.id.in_(dids)))
        db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(gids)))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == pid))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == pid))
    db_session.execute(delete(Person).where(Person.id == pid))
    db_session.commit()


def test_retry_state_guards_and_broker_fail(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    ).json()["data"]["analysis_id"]

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "PROCESSING"
    db_session.add(run)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/analyses/{analysis_id}/retry",
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 409
    )

    run.status = "REVIEWING"
    db_session.add(run)
    db_session.commit()
    assert (
        client.post(
            f"/api/v1/analyses/{analysis_id}/retry",
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 409
    )

    run.status = "FAILED"
    run.error_message = "prior"
    db_session.add(run)
    db_session.commit()

    def boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr("app.tasks.analysis_tasks.enqueue_profile_analysis", boom)
    resp = client.post(
        f"/api/v1/analyses/{analysis_id}/retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 503
    db_session.refresh(run)
    assert run.status == "FAILED"

    _cleanup_person(db_session, person.id, admin.id)


def test_exp_same_code_different_evidence_is_review_not_new():
    from app.ai.schemas.profile_candidate import (
        ExpertiseCandidate,
        ProfileCandidateDocument,
    )
    from app.modules.analysis.diff_engine import build_diffs

    candidate = ProfileCandidateDocument(
        expertise=[
            ExpertiseCandidate(code="EXP-AI-RAG", evidence_type="INFERRED", raw_value="RAG")
        ]
    )
    base = {
        "profile": {},
        "jobs": [],
        "skills": [],
        "expertise": [{"code": "EXP-AI-RAG", "evidence_type": "EXPLICIT"}],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
    }
    specs = build_diffs(candidate, base)
    exp_specs = [s for s in specs if s.entity_type == "EXP"]
    assert len(exp_specs) == 1
    assert exp_specs[0].change_type == "REVIEW"
    assert exp_specs[0].change_type != "NEW"
    assert exp_specs[0].old_value["evidence_type"] == "EXPLICIT"
    assert exp_specs[0].new_value["evidence_type"] == "INFERRED"


def test_project_relation_additive_and_unmapped_review():
    from app.ai.schemas.profile_candidate import (
        CodeRefCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
    )
    from app.modules.analysis.diff_engine import build_diffs

    pid = "11111111-1111-1111-1111-111111111111"
    base = {
        "profile": {},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [
            {
                "id": pid,
                "project_name": "P1",
                "customer_name": "C",
                "start_date": "2020",
                "end_date": "2021",
                "project_summary": "s",
                "jobs": [{"code": "JOB-PL"}],
                "skills": [
                    {"code": "TECH-LANG-PYTHON"},
                    {"code": "TECH-LANG-JAVA"},
                ],
                "expertise": [{"code": "EXP-AI-RAG", "evidence_type": "EXPLICIT"}],
                "business_domains": [],
                "customer_types": [],
            }
        ],
    }

    # A: omit Java → no removal semantics
    cand_a = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="s",
                jobs=[CodeRefCandidate(code="JOB-PL")],
                skills=[CodeRefCandidate(code="TECH-LANG-PYTHON")],
                expertise=[CodeRefCandidate(code="EXP-AI-RAG")],
            )
        ]
    )
    specs_a = build_diffs(cand_a, base)
    proj_a = [s for s in specs_a if s.entity_type == "PROJECT"]
    assert len(proj_a) == 1 and proj_a[0].change_type == "SAME"
    assert not any(
        s.field_name in {"skills", "jobs", "expertise"} and s.change_type != "SAME"
        for s in proj_a
    )

    # B: add Go → additive UPDATE only
    cand_b = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="s",
                jobs=[CodeRefCandidate(code="JOB-PL")],
                skills=[
                    CodeRefCandidate(code="TECH-LANG-PYTHON"),
                    CodeRefCandidate(code="TECH-LANG-GO", raw_value="Go"),
                ],
                expertise=[CodeRefCandidate(code="EXP-AI-RAG")],
            )
        ]
    )
    specs_b = build_diffs(cand_b, base)
    proj_b = [s for s in specs_b if s.entity_type == "PROJECT"]
    assert any(
        s.change_type == "UPDATE"
        and s.field_name == "skills"
        and isinstance(s.new_value, dict)
        and s.new_value.get("code") == "TECH-LANG-GO"
        for s in proj_b
    )
    assert not any(
        "JAVA" in str(s.new_value).upper() or "JAVA" in str(s.old_value).upper()
        for s in proj_b
        if s.change_type in {"UPDATE", "CONFLICT", "REVIEW", "NEW"}
        and s.field_name == "skills"
        and s.change_type != "UPDATE"
    )
    # Java must not appear as a removal CONFLICT/UPDATE
    assert not any(
        s.change_type == "CONFLICT" and s.field_name == "skills" for s in proj_b
    )

    # C: unmapped raw skill → REVIEW
    cand_c = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="s",
                skills=[
                    CodeRefCandidate(code="TECH-LANG-PYTHON"),
                    CodeRefCandidate(code=None, raw_value="Unknown Framework"),
                ],
            )
        ]
    )
    specs_c = build_diffs(cand_c, base)
    assert any(
        s.entity_type == "PROJECT"
        and s.change_type == "REVIEW"
        and s.field_name == "skills"
        and isinstance(s.new_value, dict)
        and s.new_value.get("raw_value") == "Unknown Framework"
        for s in specs_c
    )

    # D: same JOB relation → no job relation diffs
    cand_d = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="s",
                jobs=[CodeRefCandidate(code="JOB-PL")],
                skills=[
                    CodeRefCandidate(code="TECH-LANG-PYTHON"),
                    CodeRefCandidate(code="TECH-LANG-JAVA"),
                ],
                expertise=[CodeRefCandidate(code="EXP-AI-RAG")],
            )
        ]
    )
    specs_d = build_diffs(cand_d, base)
    proj_d = [s for s in specs_d if s.entity_type == "PROJECT"]
    assert len(proj_d) == 1 and proj_d[0].change_type == "SAME"
    assert not any(s.field_name == "jobs" for s in proj_d)

    # EXP additive on project
    cand_exp = ProfileCandidateDocument(
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C",
                start_date="2020",
                end_date="2021",
                project_summary="s",
                expertise=[
                    CodeRefCandidate(code="EXP-AI-RAG"),
                    CodeRefCandidate(code="EXP-AI-AGENT", raw_value="Agent"),
                ],
            )
        ]
    )
    specs_exp = build_diffs(cand_exp, base)
    assert any(
        s.change_type == "UPDATE"
        and s.field_name == "expertise"
        and isinstance(s.new_value, dict)
        and s.new_value.get("code") == "EXP-AI-AGENT"
        for s in specs_exp
        if s.entity_type == "PROJECT"
    )
    # omitting existing EXP-AI-RAG alone would still be SAME for expertise set
    # (not tested here); adding must not conflict-remove RAG.


def test_review_decision_is_one_shot(client: TestClient, db_session):
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    ).json()["data"]["analysis_id"]

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "REVIEWING"
    diff = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.phone",
        field_name="phone",
        change_type="NEW",
        new_value="010",
        review_status="PENDING",
    )
    db_session.add(diff)
    db_session.commit()
    db_session.refresh(diff)

    first = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{diff.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "MODIFIED", "decided_value": "010-1111-2222"},
    )
    assert first.status_code == 200, first.text
    decided_at = first.json()["data"]["decided_at"]
    decided_by = first.json()["data"]["decided_by"]

    second = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{diff.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "REJECTED"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "ANALYSIS_STATE_CONFLICT"

    db_session.refresh(diff)
    assert diff.review_status == "MODIFIED"
    assert diff.decided_value == "010-1111-2222"
    assert str(diff.decided_by) == decided_by
    assert diff.decided_at is not None

    _cleanup_person(db_session, person.id, admin.id)


def test_bulk_mixed_pending_and_decided_is_atomic_409(client: TestClient, db_session):
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun

    admin = _create_user(
        db_session, login_id=f"a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    ).json()["data"]["analysis_id"]

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "REVIEWING"
    d_pending = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={"code": "JOB-AI-DEV"},
        review_status="PENDING",
    )
    d_done = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="TECH",
        candidate_path="skills[0]",
        change_type="NEW",
        new_value={"code": "TECH-LANG-PYTHON"},
        review_status="ACCEPTED",
    )
    db_session.add_all([d_pending, d_done])
    db_session.commit()
    db_session.refresh(d_pending)
    db_session.refresh(d_done)

    resp = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(d_pending.id), str(d_done.id)],
            "review_status": "ACCEPTED",
        },
    )
    assert resp.status_code == 409
    db_session.refresh(d_pending)
    assert d_pending.review_status == "PENDING"

    # Bulk MODIFIED rejected
    bad_status = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(d_pending.id)], "review_status": "MODIFIED"},
    )
    assert bad_status.status_code == 400

    _cleanup_person(db_session, person.id, admin.id)


def test_source_ref_outside_analysis_page_limit_discarded(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings
    from app.modules.analysis.normalize import normalize_candidate

    get_settings.cache_clear()
    monkeypatch.setenv("ANALYSIS_MAX_PAGES_PER_DOCUMENT", "2")
    get_settings.cache_clear()
    settings = get_settings()
    assert int(settings.analysis_max_pages_per_document) == 2

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    # Mimic service: only first N pages are allowed for source_ref validation.
    max_pages = int(settings.analysis_max_pages_per_document)
    all_pages = {1, 2, 3, 50}
    allowed = {doc_id: {p for p in all_pages if p <= max_pages}}
    assert 50 not in allowed[doc_id]

    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "홍길동"},
        "jobs": [
            {
                "code": None,
                "raw_value": "Dev",
                "source_refs": [
                    {"document_id": doc_id, "page_no": 1, "quote_text": "page1"},
                    {"document_id": doc_id, "page_no": 50, "quote_text": "page50"},
                ],
            }
        ],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "summary": {},
        "analysis": {},
    }
    doc = normalize_candidate(
        raw,
        catalog={},
        allowed_documents=allowed,
        settings=settings,
        page_texts={(doc_id, 1): "page1 text", (doc_id, 50): "page50 text"},
    )
    pages = {r.page_no for r in doc.jobs[0].source_refs}
    assert 1 in pages
    assert 50 not in pages
    get_settings.cache_clear()


def test_code_catalog_includes_aliases():
    from types import SimpleNamespace

    from app.modules.analysis.service import AnalysisService

    codes = [
        SimpleNamespace(code="TECH-LANG-PYTHON", code_type="TECH", name="Python"),
        SimpleNamespace(code="JOB-AI-DEV", code_type="JOB", name="AI Engineer"),
    ]
    aliases = {
        "TECH-LANG-PYTHON": ["Py", "Python3"],
        "JOB-AI-DEV": ["AI개발"],
    }
    # Build without DB session methods — call formatter via unbound style
    text = AnalysisService._format_code_catalog(
        SimpleNamespace(
            settings=SimpleNamespace(analysis_code_context_max_chars=10_000)
        ),
        codes,
        aliases,
    )
    assert "TECH-LANG-PYTHON\tPython\tPy|Python3" in text
    assert "JOB-AI-DEV\tAI Engineer\tAI개발" in text
