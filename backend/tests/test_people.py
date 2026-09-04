"""People core API tests — only cleans data created by each test."""

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
os.environ.setdefault("APP_ENV", "test")


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

    get_settings.cache_clear()
    get_redis.cache_clear()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()


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
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _ensure_code(db_session, code: str, code_type: str, name: str) -> None:
    from app.db.models.code import CodeMaster

    existing = db_session.get(CodeMaster, code)
    if existing is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type=code_type,
                name=name,
                sort_order=0,
                is_active=True,
            )
        )
        db_session.commit()


def _cleanup_person(db_session, person_id) -> None:
    from app.db.models.person import (
        Person,
        PersonExpertise,
        PersonJob,
        PersonProfile,
        PersonSkill,
    )
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "PERSON", AuditLog.target_id == person_id
        )
    )
    db_session.execute(delete(PersonJob).where(PersonJob.person_id == person_id))
    db_session.execute(delete(PersonSkill).where(PersonSkill.person_id == person_id))
    db_session.execute(
        delete(PersonExpertise).where(PersonExpertise.person_id == person_id)
    )
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


def test_people_list_permissions(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        assert client.get("/api/v1/people").status_code == 401
        _login(client, user.login_id)
        assert client.get("/api/v1/people").status_code == 200
        csrf = client.cookies.get("ts_csrf")
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, admin.login_id)
        assert client.get("/api/v1/people").status_code == 200
    finally:
        _cleanup_user(db_session, user.id)
        _cleanup_user(db_session, admin.id)


def test_create_profile_sets_revision_and_index_job(client: TestClient, db_session) -> None:
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        response = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={
                "name": f"홍길동_{suffix}",
                "technical_grade": "EXPERT",
                "career_confirmed_months": 120,
                "affiliation_company": f"ABC_{suffix}",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        person_id = uuid.UUID(data["id"])
        assert data["profile_version"] == 1
        assert data["profile"]["name"].startswith("홍길동_")
        assert "password" not in str(data)

        rev = db_session.execute(
            select(ProfileRevision).where(ProfileRevision.person_id == person_id)
        ).scalar_one()
        assert rev.revision_no == 1
        assert rev.source_type == "USER"
        assert "profile" in rev.snapshot_json

        job = db_session.execute(
            select(SearchIndexJob).where(SearchIndexJob.person_id == person_id)
        ).scalar_one()
        assert job.action == "REBUILD_PERSON"
        assert job.status == "PENDING"
        assert job.idempotency_key == f"people:{person_id}:profile:1:rebuild"

        audits = list(
            db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == person_id,
                    AuditLog.action_type == "PERSON_CREATE",
                )
            ).scalars()
        )
        assert audits
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


def test_user_cannot_mutate_people(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": f"P_{suffix}"},
        )
        person_id = uuid.UUID(created.json()["data"]["id"])
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

        ucsrf = _login(client, user.login_id)
        assert (
            client.post(
                "/api/v1/people",
                headers={"X-CSRF-Token": ucsrf},
                json={"name": "Nope"},
            ).status_code
            == 403
        )
        assert (
            client.patch(
                f"/api/v1/people/{person_id}/profile",
                headers={"X-CSRF-Token": ucsrf},
                json={"expected_profile_version": 1, "name": "X"},
            ).status_code
            == 403
        )
        assert client.get(f"/api/v1/people/{person_id}").status_code == 200
        assert client.get(f"/api/v1/people/{person_id}/revisions").status_code == 403
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, user.id)
        _cleanup_user(db_session, admin.id)


def test_profile_optimistic_lock(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": f"Lock_{suffix}"},
        )
        person_id = uuid.UUID(created.json()["data"]["id"])

        ok = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 1, "name": f"Lock2_{suffix}"},
        )
        assert ok.status_code == 200
        assert ok.json()["data"]["profile_version"] == 2

        stale = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 1, "name": "Stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "PROFILE_VERSION_CONFLICT"
        assert client.get(f"/api/v1/people/{person_id}").json()["data"]["profile"]["name"].startswith(
            "Lock2_"
        )
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


def test_jobs_skills_expertise_and_filters(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    job = f"JOB-AI-{suffix}"
    tech = f"TECH-PY-{suffix}"
    exp = f"EXP-RAG-{suffix}"
    _ensure_code(db_session, job, "JOB", "AI개발자")
    _ensure_code(db_session, tech, "TECH", "Python")
    _ensure_code(db_session, exp, "EXP", "RAG")
    # inactive code still displayable when linked
    inactive_tech = f"TECH-OLD-{suffix}"
    _ensure_code(db_session, inactive_tech, "TECH", "Legacy")
    from app.db.models.code import CodeMaster

    row = db_session.get(CodeMaster, inactive_tech)
    row.is_active = False
    db_session.add(row)
    db_session.commit()

    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={
                "name": f"Filter_{suffix}",
                "affiliation_company": f"Corp_{suffix}",
                "technical_grade": "ADVANCED",
            },
        )
        person_id = uuid.UUID(created.json()["data"]["id"])

        jobs = client.put(
            f"/api/v1/people/{person_id}/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 1,
                "jobs": [{"job_code": job, "job_type": "PRIMARY", "sort_order": 0}],
            },
        )
        assert jobs.status_code == 200, jobs.text
        assert jobs.json()["data"]["profile_version"] == 2

        bad_type = client.put(
            f"/api/v1/people/{person_id}/skills",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 2,
                "skills": [{"tech_code": exp}],
            },
        )
        assert bad_type.status_code == 400
        assert bad_type.json()["code"] == "INVALID_TECH_CODE"

        skills = client.put(
            f"/api/v1/people/{person_id}/skills",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 2,
                "skills": [
                    {
                        "tech_code": tech,
                        "experience_months": 60,
                        "is_representative": True,
                    },
                    {"tech_code": inactive_tech, "is_representative": False},
                ],
            },
        )
        assert skills.status_code == 200, skills.text

        expertise = client.put(
            f"/api/v1/people/{person_id}/expertise",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 3,
                "expertise": [{"exp_code": exp, "evidence_type": "EXPLICIT"}],
            },
        )
        assert expertise.status_code == 200, expertise.text
        assert expertise.json()["data"]["profile_version"] == 4

        detail = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert any(s["code"] == inactive_tech for s in detail["skills"])
        assert any(e["code"] == exp and e["name"] == "RAG" for e in detail["expertise"])

        listed = client.get(
            "/api/v1/people",
            params={
                "q": f"Filter_{suffix}",
                "job_codes": job,
                "tech_codes": tech,
                "exp_codes": exp,
                "grade": "ADVANCED",
                "affiliation": f"Corp_{suffix}",
            },
        )
        assert listed.status_code == 200
        assert any(item["id"] == str(person_id) for item in listed.json()["data"])

        # AND across categories: wrong tech should exclude
        miss = client.get(
            "/api/v1/people",
            params={"job_codes": job, "tech_codes": "TECH-NOPE"},
        )
        assert miss.status_code == 200
        assert all(item["id"] != str(person_id) for item in miss.json()["data"])

        # duplicate skill rejected
        dup = client.put(
            f"/api/v1/people/{person_id}/skills",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 4,
                "skills": [{"tech_code": tech}, {"tech_code": tech}],
            },
        )
        assert dup.status_code == 400
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        for code in (job, tech, exp, inactive_tech):
            from app.db.models.code import CodeAlias, CodeMaster

            db_session.execute(delete(CodeAlias).where(CodeAlias.code == code))
            db_session.execute(delete(CodeMaster).where(CodeMaster.code == code))
        db_session.commit()
        _cleanup_user(db_session, admin.id)


def test_status_soft_delete_and_csrf(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": f"Del_{suffix}"},
        )
        person_id = uuid.UUID(created.json()["data"]["id"])

        no_csrf = client.patch(
            f"/api/v1/people/{person_id}",
            json={"status": "ARCHIVED"},
        )
        assert no_csrf.status_code == 403

        archived = client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "ARCHIVED"},
        )
        assert archived.status_code == 200
        assert archived.json()["data"]["profile_version"] == 1

        deleted = client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "DELETED"},
        )
        assert deleted.status_code == 200
        from app.db.models.person import Person

        person = db_session.get(Person, person_id)
        assert person.status == "DELETED"
        assert person.deleted_at is not None

        default_list = client.get("/api/v1/people", params={"q": f"Del_{suffix}"})
        assert all(item["id"] != str(person_id) for item in default_list.json()["data"])
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


def test_revisions_latest_first(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": f"Rev_{suffix}"},
        )
        person_id = uuid.UUID(created.json()["data"]["id"])
        client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 1, "profile_summary": "updated"},
        )
        revs = client.get(f"/api/v1/people/{person_id}/revisions")
        assert revs.status_code == 200
        data = revs.json()["data"]
        assert [r["revision_no"] for r in data] == [2, 1]
        assert data[0]["snapshot"]["profile"]["profile_summary"] == "updated"
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


def test_profile_patch_name_and_length_validation(client: TestClient, db_session) -> None:
    """Omit vs explicit null for name; VARCHAR max lengths rejected as 4xx."""
    from app.db.models.person import PersonProfile

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    person_id = None
    original_name = f"Keep_{suffix}"
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": original_name, "email": "a@example.com"},
        )
        assert created.status_code == 201, created.text
        person_id = uuid.UUID(created.json()["data"]["id"])

        omit = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 1, "profile_summary": "summary-only"},
        )
        assert omit.status_code == 200, omit.text
        assert omit.json()["data"]["profile"]["name"] == original_name
        assert omit.json()["data"]["profile_version"] == 2

        renamed = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 2, "name": f"New_{suffix}"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["profile"]["name"] == f"New_{suffix}"
        assert renamed.json()["data"]["profile_version"] == 3

        for bad_name in (None, "", "   "):
            bad = client.patch(
                f"/api/v1/people/{person_id}/profile",
                headers={"X-CSRF-Token": csrf},
                json={"expected_profile_version": 3, "name": bad_name},
            )
            assert bad.status_code == 422, bad.text
            db_session.expire_all()
            profile = db_session.get(PersonProfile, person_id)
            assert profile is not None
            assert profile.name == f"New_{suffix}"
            assert profile.profile_version == 3

        email_ok = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 3, "email": "e" * 243 + "@example.com"},
        )
        assert email_ok.status_code == 200, email_ok.text
        assert len(email_ok.json()["data"]["profile"]["email"]) == 255
        assert email_ok.json()["data"]["profile_version"] == 4

        email_over = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 4, "email": "x" * 256},
        )
        assert email_over.status_code == 422, email_over.text

        aff_over = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 4, "affiliation_company": "A" * 301},
        )
        assert aff_over.status_code == 422, aff_over.text

        phone_over = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_profile_version": 4, "phone": "1" * 51},
        )
        assert phone_over.status_code == 422, phone_over.text

        # Failed validation must not leave the session unusable.
        recover = client.patch(
            f"/api/v1/people/{person_id}/profile",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_profile_version": 4,
                "department": f"Dept_{suffix}",
            },
        )
        assert recover.status_code == 200, recover.text
        assert recover.json()["data"]["profile_version"] == 5
        assert recover.json()["data"]["profile"]["name"] == f"New_{suffix}"
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)


def test_deleted_person_detail_rbac_and_restore(client: TestClient, db_session) -> None:
    """USER cannot see DELETED detail; ADMIN can view and restore via status."""
    from app.db.models.person import Person

    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/people",
            headers={"X-CSRF-Token": csrf},
            json={"name": f"Gone_{suffix}"},
        )
        assert created.status_code == 201, created.text
        person_id = uuid.UUID(created.json()["data"]["id"])

        deleted = client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "DELETED"},
        )
        assert deleted.status_code == 200, deleted.text

        admin_detail = client.get(f"/api/v1/people/{person_id}")
        assert admin_detail.status_code == 200, admin_detail.text
        assert admin_detail.json()["data"]["status"] == "DELETED"

        # Switch to USER session
        admin_csrf = client.cookies.get("ts_csrf")
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": admin_csrf})
        _login(client, user.login_id)

        user_detail = client.get(f"/api/v1/people/{person_id}")
        assert user_detail.status_code == 404, user_detail.text

        user_list = client.get("/api/v1/people", params={"q": f"Gone_{suffix}"})
        assert user_list.status_code == 200
        assert all(item["id"] != str(person_id) for item in user_list.json()["data"])

        user_deleted_filter = client.get("/api/v1/people", params={"status": "DELETED"})
        assert user_deleted_filter.status_code == 400, user_deleted_filter.text

        user_csrf = client.cookies.get("ts_csrf")
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": user_csrf})
        csrf = _login(client, admin.login_id)

        restored = client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "ACTIVE"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["data"]["status"] == "ACTIVE"

        db_session.expire_all()
        person = db_session.get(Person, person_id)
        assert person is not None
        assert person.status == "ACTIVE"
        assert person.deleted_at is None

        # USER can see restored person again
        admin_csrf = client.cookies.get("ts_csrf")
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": admin_csrf})
        _login(client, user.login_id)
        assert client.get(f"/api/v1/people/{person_id}").status_code == 200
    finally:
        if person_id:
            _cleanup_person(db_session, person_id)
        _cleanup_user(db_session, admin.id)
        _cleanup_user(db_session, user.id)
