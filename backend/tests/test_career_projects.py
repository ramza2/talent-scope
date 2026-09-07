"""Project / career history API tests — only cleans data created by each test."""

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


def _cleanup_codes(db_session, codes: list[str]) -> None:
    from app.db.models.code import CodeMaster

    for code in codes:
        db_session.execute(delete(CodeMaster).where(CodeMaster.code == code))
    db_session.commit()


def _cleanup_person(db_session, person_id) -> None:
    from app.db.models.person import (
        Certification,
        Education,
        EmploymentHistory,
        Person,
        PersonExpertise,
        PersonJob,
        PersonProfile,
        PersonSkill,
    )
    from app.db.models.project import (
        Project,
        ProjectBusinessDomain,
        ProjectCustomerType,
        ProjectExpertise,
        ProjectJob,
        ProjectSkill,
    )
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    project_ids = list(
        db_session.execute(
            select(Project.id).where(Project.person_id == person_id)
        ).scalars().all()
    )
    for pid in project_ids:
        db_session.execute(delete(ProjectJob).where(ProjectJob.project_id == pid))
        db_session.execute(delete(ProjectSkill).where(ProjectSkill.project_id == pid))
        db_session.execute(
            delete(ProjectExpertise).where(ProjectExpertise.project_id == pid)
        )
        db_session.execute(
            delete(ProjectBusinessDomain).where(
                ProjectBusinessDomain.project_id == pid
            )
        )
        db_session.execute(
            delete(ProjectCustomerType).where(ProjectCustomerType.project_id == pid)
        )
    db_session.execute(delete(Project).where(Project.person_id == person_id))
    db_session.execute(
        delete(EmploymentHistory).where(EmploymentHistory.person_id == person_id)
    )
    db_session.execute(delete(Education).where(Education.person_id == person_id))
    db_session.execute(
        delete(Certification).where(Certification.person_id == person_id)
    )
    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "PERSON", AuditLog.target_id == person_id
        )
    )
    db_session.execute(
        delete(AuditLog).where(
            AuditLog.metadata_json["person_id"].as_string() == str(person_id)
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


def _create_person(client: TestClient, csrf: str, name: str) -> str:
    response = client.post(
        "/api/v1/people",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def test_project_crud_relations_revision_and_aggregate(
    client: TestClient, db_session
) -> None:
    from app.db.models.person import PersonProfile
    from app.db.models.project import Project
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    suffix = uuid.uuid4().hex[:8]
    codes = {
        "job": f"JOB-T-{suffix}",
        "tech": f"TECH-LANG-PY-{suffix}",
        "exp": f"EXP-AI-RAG-{suffix}",
        "biz": f"BIZ-DEF-{suffix}",
        "cust": f"CUSTOMER-TYPE-MIL-{suffix}",
        "wrong": f"TECH-WRONG-{suffix}",
    }
    _ensure_code(db_session, codes["job"], "JOB", "PL")
    _ensure_code(db_session, codes["tech"], "TECH", "Python")
    _ensure_code(db_session, codes["exp"], "EXP", "RAG")
    _ensure_code(db_session, codes["biz"], "BIZ", "국방")
    _ensure_code(db_session, codes["cust"], "CUSTOMER_TYPE", "군")
    _ensure_code(db_session, codes["wrong"], "TECH", "WrongAsJob")

    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    person_id = None
    project_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"프로젝트인력_{suffix}")

        # USER can GET, cannot mutate
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        assert client.get(f"/api/v1/people/{person_id}/projects").status_code == 200
        forbidden = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": client.cookies.get("ts_csrf")},
            json={"project_name": "x"},
        )
        assert forbidden.status_code == 403

        csrf = _login(client, admin.login_id)
        no_csrf = client.post(
            f"/api/v1/people/{person_id}/projects",
            json={"project_name": "no csrf"},
        )
        assert no_csrf.status_code == 403

        # Invalid code type
        bad = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "Bad",
                "job_codes": [codes["wrong"]],
            },
        )
        assert bad.status_code == 400
        assert bad.json()["code"] == "INVALID_PROJECT_JOB_CODE"

        # Duplicate codes
        dup = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "Dup",
                "tech_codes": [codes["tech"], codes["tech"]],
            },
        )
        assert dup.status_code == 400

        # Bad dates
        dates = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "Dates",
                "start_date": "2025-12-31",
                "end_date": "2025-01-01",
            },
        )
        assert dates.status_code == 422

        created = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "군 의료 AI 플랫폼 구축",
                "customer_name": "OO기관",
                "start_date": "2026-08-01",
                "end_date": "2027-07-31",
                "duration_months": 12,
                "responsibilities": "AI/Backend",
                "project_summary": "요약",
                "job_codes": [codes["job"]],
                "tech_codes": [codes["tech"]],
                "expertise": [{"exp_code": codes["exp"], "evidence_type": "EXPLICIT"}],
                "biz_codes": [codes["biz"]],
                "customer_type_codes": [codes["cust"]],
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()["data"]
        project_id = body["id"]
        assert body["source_type"] == "USER"
        assert body["jobs"][0]["name"] == "PL"
        assert body["skills"][0]["name"] == "Python"
        assert body["expertise"][0]["code"] == codes["exp"]
        assert body["business_domains"][0]["code"] == codes["biz"]
        assert body["customer_types"][0]["code"] == codes["cust"]

        detail = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert detail["profile_version"] == 2
        assert any(p["id"] == project_id for p in detail["recent_projects"])
        assert any(b["code"] == codes["biz"] for b in detail["business_domains"])
        assert any(c["code"] == codes["cust"] for c in detail["customer_types"])

        rev = db_session.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == uuid.UUID(person_id),
                ProfileRevision.revision_no == 2,
            )
        ).scalar_one()
        snap = rev.snapshot_json
        assert "projects" in snap
        assert snap["projects"][0]["project_name"] == "군 의료 AI 플랫폼 구축"
        assert snap["projects"][0]["jobs"][0]["code"] == codes["job"]
        assert snap["projects"][0]["business_domains"][0]["code"] == codes["biz"]

        audit = db_session.execute(
            select(AuditLog).where(
                AuditLog.action_type == "PROJECT_CREATE",
                AuditLog.target_id == uuid.UUID(project_id),
            )
        ).scalar_one()
        assert audit.target_type == "PROJECT"
        assert audit.after_json["job_codes"] == [codes["job"]]

        job = db_session.execute(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == uuid.UUID(person_id),
                SearchIndexJob.idempotency_key
                == f"people:{person_id}:profile:2:rebuild",
            )
        ).scalar_one()
        assert job.action == "REBUILD_PERSON"
        assert job.status == "PENDING"

        # PATCH: omit tech keeps it; empty biz clears; preserve analysis run id
        db_session.execute(
            select(Project).where(Project.id == uuid.UUID(project_id))
        )
        project_row = db_session.get(Project, uuid.UUID(project_id))
        fake_run = uuid.uuid4()
        # Set provenance without FK (analysis_run may not exist) via raw update skipping FK
        # Prefer leaving null and asserting it stays null after USER edit.
        assert project_row.source_analysis_run_id is None

        patched = client.patch(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "군 의료 AI 플랫폼 구축(수정)",
                "biz_codes": [],
            },
        )
        assert patched.status_code == 200, patched.text
        pdata = patched.json()["data"]
        assert pdata["project_name"].endswith("(수정)")
        assert pdata["skills"][0]["code"] == codes["tech"]
        assert pdata["business_domains"] == []
        assert pdata["source_type"] == "USER"
        assert pdata["source_analysis_run_id"] is None

        detail2 = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert detail2["profile_version"] == 3
        assert not any(b["code"] == codes["biz"] for b in detail2["business_domains"])
        assert any(c["code"] == codes["cust"] for c in detail2["customer_types"])

        # Soft delete
        deleted = client.delete(
            f"/api/v1/projects/{project_id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert deleted.status_code == 204
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
        listed = client.get(f"/api/v1/people/{person_id}/projects").json()["data"]
        assert listed == []
        detail3 = client.get(f"/api/v1/people/{person_id}").json()["data"]
        assert detail3["profile_version"] == 4
        assert detail3["recent_projects"] == []
        assert detail3["business_domains"] == []
        assert detail3["customer_types"] == []

        db_session.expire_all()
        soft = db_session.get(Project, uuid.UUID(project_id))
        assert soft is not None
        assert soft.deleted_at is not None

        rev_del = db_session.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == uuid.UUID(person_id),
                ProfileRevision.revision_no == 4,
            )
        ).scalar_one()
        assert rev_del.snapshot_json["projects"] == []
        # Prior revision still has the project
        assert rev.snapshot_json["projects"][0]["id"] == project_id

        # Filters: recreate project and filter by job/biz/period
        created2 = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "필터용",
                "start_date": "2025-03-01",
                "end_date": "2025-09-30",
                "job_codes": [codes["job"]],
                "biz_codes": [codes["biz"]],
            },
        )
        assert created2.status_code == 201
        filtered = client.get(
            f"/api/v1/people/{person_id}/projects"
            f"?job_codes={codes['job']}&biz_codes={codes['biz']}&from=2025-01&to=2025-12"
        )
        assert filtered.status_code == 200
        assert len(filtered.json()["data"]) == 1
        miss = client.get(
            f"/api/v1/people/{person_id}/projects?from=2020-01&to=2020-12"
        )
        assert miss.json()["data"] == []

        profile = db_session.get(PersonProfile, uuid.UUID(person_id))
        assert profile.profile_version == 5
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, list(codes.values()))
        _cleanup_user(db_session, admin.id)
        _cleanup_user(db_session, user.id)


def test_employment_education_certification_and_version_flow(
    client: TestClient, db_session
) -> None:
    from app.db.models.person import Certification, Education, EmploymentHistory, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob

    suffix = uuid.uuid4().hex[:8]
    codes = {
        "job": f"JOB-C-{suffix}",
        "tech": f"TECH-C-{suffix}",
        "exp": f"EXP-C-{suffix}",
        "biz": f"BIZ-C-{suffix}",
        "cust": f"CUSTOMER-TYPE-C-{suffix}",
    }
    for code, ctype, name in [
        (codes["job"], "JOB", "AI개발"),
        (codes["tech"], "TECH", "FastAPI"),
        (codes["exp"], "EXP", "RAG"),
        (codes["biz"], "BIZ", "공공"),
        (codes["cust"], "CUSTOMER_TYPE", "공공기관"),
    ]:
        _ensure_code(db_session, code, ctype, name)

    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    person_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"경력인력_{suffix}")
        # v1 at create

        # USER mutation forbidden
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        user_csrf = _login(client, user.login_id)
        assert (
            client.post(
                f"/api/v1/people/{person_id}/employment-history",
                headers={"X-CSRF-Token": user_csrf},
                json={"company_name": "X"},
            ).status_code
            == 403
        )
        assert client.get(f"/api/v1/people/{person_id}/employment-history").status_code == 200
        assert client.get(f"/api/v1/people/{person_id}/education").status_code == 200
        assert client.get(f"/api/v1/people/{person_id}/certifications").status_code == 200

        csrf = _login(client, admin.login_id)

        # Project create → v2
        proj = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "통합 스냅샷 프로젝트",
                "job_codes": [codes["job"]],
                "tech_codes": [codes["tech"]],
                "expertise": [{"exp_code": codes["exp"]}],
                "biz_codes": [codes["biz"]],
                "customer_type_codes": [codes["cust"]],
            },
        )
        assert proj.status_code == 201, proj.text
        project_id = proj.json()["data"]["id"]

        # Employment → v3
        emp = client.post(
            f"/api/v1/people/{person_id}/employment-history",
            headers={"X-CSRF-Token": csrf},
            json={
                "company_name": "ABC테크",
                "department": "AI연구팀",
                "title": "수석",
                "start_date": "2020-01-01",
                "responsibilities": "연구",
            },
        )
        assert emp.status_code == 201, emp.text
        emp_id = emp.json()["data"]["id"]
        assert emp.json()["data"]["source_type"] == "USER"

        profile = db_session.get(PersonProfile, uuid.UUID(person_id))
        db_session.refresh(profile)
        career_before = profile.career_calculated_months

        # blank company rejected
        assert (
            client.post(
                f"/api/v1/people/{person_id}/employment-history",
                headers={"X-CSRF-Token": csrf},
                json={"company_name": "   "},
            ).status_code
            == 422
        )
        assert (
            client.patch(
                f"/api/v1/employment-history/{emp_id}",
                headers={"X-CSRF-Token": csrf},
                json={"company_name": None},
            ).status_code
            == 422
        )
        assert (
            client.patch(
                f"/api/v1/employment-history/{emp_id}",
                headers={"X-CSRF-Token": csrf},
                json={"start_date": "2022-01-01", "end_date": "2021-01-01"},
            ).status_code
            == 422
        )

        # Education → v4
        edu = client.post(
            f"/api/v1/people/{person_id}/education",
            headers={"X-CSRF-Token": csrf},
            json={
                "school_name": "한국대학교",
                "major": "컴퓨터공학",
                "degree": "학사",
                "start_date": "2012-03-01",
                "end_date": "2016-02-28",
                "status": "졸업",
            },
        )
        assert edu.status_code == 201, edu.text
        edu_id = edu.json()["data"]["id"]

        # Certification → v5
        cert = client.post(
            f"/api/v1/people/{person_id}/certifications",
            headers={"X-CSRF-Token": csrf},
            json={
                "certification_name": "정보처리기사",
                "issuer": "한국산업인력공단",
                "acquired_date": "2018-05-01",
                "certificate_no": f"CERT-{suffix}",
            },
        )
        assert cert.status_code == 201, cert.text
        cert_id = cert.json()["data"]["id"]

        # Project update → v6
        assert (
            client.patch(
                f"/api/v1/projects/{project_id}",
                headers={"X-CSRF-Token": csrf},
                json={"project_summary": "업데이트"},
            ).status_code
            == 200
        )

        db_session.expire_all()
        profile = db_session.get(PersonProfile, uuid.UUID(person_id))
        assert profile.profile_version == 6
        assert profile.career_calculated_months == career_before

        # Full snapshot check at v6
        rev6 = db_session.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == uuid.UUID(person_id),
                ProfileRevision.revision_no == 6,
            )
        ).scalar_one()
        snap = rev6.snapshot_json
        assert snap["projects"]
        assert snap["employment_history"]
        assert snap["education"]
        assert snap["certifications"]
        assert "certificate_no" not in snap["certifications"][0]
        assert snap["projects"][0]["skills"][0]["code"] == codes["tech"]

        cert_audit = db_session.execute(
            select(AuditLog).where(
                AuditLog.action_type == "CERTIFICATION_CREATE",
                AuditLog.target_id == uuid.UUID(cert_id),
            )
        ).scalar_one()
        assert "certificate_no" not in (cert_audit.after_json or {})

        # Education delete → v7, hard delete
        assert (
            client.delete(
                f"/api/v1/education/{edu_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert db_session.get(Education, uuid.UUID(edu_id)) is None
        rev7 = db_session.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == uuid.UUID(person_id),
                ProfileRevision.revision_no == 7,
            )
        ).scalar_one()
        assert rev7.snapshot_json["education"] == []
        # old revision keeps education
        assert rev6.snapshot_json["education"]

        # Employment hard delete
        assert (
            client.delete(
                f"/api/v1/employment-history/{emp_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert db_session.get(EmploymentHistory, uuid.UUID(emp_id)) is None

        # Certification hard delete
        assert (
            client.delete(
                f"/api/v1/certifications/{cert_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert db_session.get(Certification, uuid.UUID(cert_id)) is None

        db_session.expire_all()
        profile = db_session.get(PersonProfile, uuid.UUID(person_id))
        assert profile.profile_version == 9

        jobs = list(
            db_session.execute(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == uuid.UUID(person_id)
                )
            ).scalars().all()
        )
        assert any(j.payload_json.get("profile_version") == 9 for j in jobs)

        # Revision numbers 1..9 sequential
        revs = list(
            db_session.execute(
                select(ProfileRevision.revision_no)
                .where(ProfileRevision.person_id == uuid.UUID(person_id))
                .order_by(ProfileRevision.revision_no.asc())
            ).scalars().all()
        )
        assert revs == list(range(1, 10))
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, list(codes.values()))
        _cleanup_user(db_session, admin.id)
        _cleanup_user(db_session, user.id)


def test_deleted_person_hides_project_and_career_from_user(
    client: TestClient, db_session
) -> None:
    """DELETED Person nested GETs follow People Core visibility (USER 404)."""
    suffix = uuid.uuid4().hex[:8]
    codes = {
        "job": f"JOB-D-{suffix}",
        "tech": f"TECH-D-{suffix}",
        "exp": f"EXP-D-{suffix}",
        "biz": f"BIZ-D-{suffix}",
        "cust": f"CUSTOMER-TYPE-D-{suffix}",
    }
    for code, ctype, name in [
        (codes["job"], "JOB", "PL"),
        (codes["tech"], "TECH", "Python"),
        (codes["exp"], "EXP", "RAG"),
        (codes["biz"], "BIZ", "국방"),
        (codes["cust"], "CUSTOMER_TYPE", "군"),
    ]:
        _ensure_code(db_session, code, ctype, name)

    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    person_id = None
    project_id = None
    try:
        csrf = _login(client, admin.login_id)
        person_id = _create_person(client, csrf, f"삭제RBAC_{suffix}")

        proj = client.post(
            f"/api/v1/people/{person_id}/projects",
            headers={"X-CSRF-Token": csrf},
            json={
                "project_name": "삭제전프로젝트",
                "job_codes": [codes["job"]],
                "tech_codes": [codes["tech"]],
                "expertise": [{"exp_code": codes["exp"]}],
                "biz_codes": [codes["biz"]],
                "customer_type_codes": [codes["cust"]],
            },
        )
        assert proj.status_code == 201, proj.text
        project_id = proj.json()["data"]["id"]

        assert (
            client.post(
                f"/api/v1/people/{person_id}/employment-history",
                headers={"X-CSRF-Token": csrf},
                json={"company_name": "삭제전회사"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/people/{person_id}/education",
                headers={"X-CSRF-Token": csrf},
                json={"school_name": "삭제전학교"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/people/{person_id}/certifications",
                headers={"X-CSRF-Token": csrf},
                json={"certification_name": "삭제전자격"},
            ).status_code
            == 201
        )

        status_resp = client.patch(
            f"/api/v1/people/{person_id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "DELETED"},
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["data"]["status"] == "DELETED"

        # ADMIN can still read person + nested resources + project detail
        assert client.get(f"/api/v1/people/{person_id}").status_code == 200
        assert client.get(f"/api/v1/people/{person_id}/projects").status_code == 200
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
        assert (
            client.get(f"/api/v1/people/{person_id}/employment-history").status_code
            == 200
        )
        assert client.get(f"/api/v1/people/{person_id}/education").status_code == 200
        assert (
            client.get(f"/api/v1/people/{person_id}/certifications").status_code == 200
        )

        # USER: all return 404 (do not reveal existence)
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        for path in (
            f"/api/v1/people/{person_id}",
            f"/api/v1/people/{person_id}/projects",
            f"/api/v1/projects/{project_id}",
            f"/api/v1/people/{person_id}/employment-history",
            f"/api/v1/people/{person_id}/education",
            f"/api/v1/people/{person_id}/certifications",
        ):
            resp = client.get(path)
            assert resp.status_code == 404, (path, resp.status_code, resp.text)
            assert resp.json()["code"] == "NOT_FOUND"

        # Soft-deleted project remains 404 for ADMIN as well
        csrf = _login(client, admin.login_id)
        # Restore person first so we can soft-delete the project via API
        assert (
            client.patch(
                f"/api/v1/people/{person_id}",
                headers={"X-CSRF-Token": csrf},
                json={"status": "ACTIVE"},
            ).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/v1/projects/{project_id}",
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 204
        )
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
    finally:
        if person_id:
            _cleanup_person(db_session, uuid.UUID(person_id))
        _cleanup_codes(db_session, list(codes.values()))
        _cleanup_user(db_session, admin.id)
        _cleanup_user(db_session, user.id)
