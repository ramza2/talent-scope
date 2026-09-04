"""Users admin API tests."""

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


def _create_user(
    db_session,
    *,
    login_id: str,
    password: str,
    role: str = "USER",
    status: str = "ACTIVE",
    name: str = "Tester",
):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id,
        password_hash=hash_password(password),
        name=name,
        role=role,
        status=status,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _cleanup_user(db_session, user_id) -> None:
    from app.db.models.revision import AuditLog
    from app.db.models.user import AppUser

    db_session.execute(delete(AuditLog).where(AuditLog.target_id == user_id))
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


def test_list_users_permissions(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        assert client.get("/api/v1/users").status_code == 401
        csrf = _login(client, user.login_id)
        forbidden = client.get("/api/v1/users")
        assert forbidden.status_code == 403
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, admin.login_id)
        ok = client.get("/api/v1/users", params={"q": admin.login_id})
        assert ok.status_code == 200
        body = ok.json()
        assert "meta" in body
        assert body["meta"]["page"] == 1
        assert all("password" not in item for item in body["data"])
        assert all("password_hash" not in item for item in body["data"])
    finally:
        _cleanup_user(db_session, user.id)
        _cleanup_user(db_session, admin.id)


def test_create_user_and_duplicate(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    created_id = None
    try:
        csrf = _login(client, admin.login_id)
        response = client.post(
            "/api/v1/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "login_id": f"hong_{suffix}",
                "name": "홍길동",
                "email": "hong@example.com",
                "department": "기술연구소",
                "role": "USER",
                "password": "Initial1!",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        created_id = data["id"]
        assert data["login_id"] == f"hong_{suffix}"
        assert "password" not in data
        assert "password_hash" not in data

        from app.core.security import verify_password
        from app.db.models.user import AppUser

        row = db_session.execute(
            select(AppUser).where(AppUser.id == uuid.UUID(created_id))
        ).scalar_one()
        assert verify_password("Initial1!", row.password_hash)
        assert row.password_hash != "Initial1!"

        dup = client.post(
            "/api/v1/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "login_id": f"hong_{suffix}",
                "name": "홍길동2",
                "role": "USER",
                "password": "Initial1!",
            },
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == "USER_LOGIN_ID_EXISTS"

        # New user can login
        other = TestClient(client.app)
        login = other.post(
            "/api/v1/auth/login",
            json={"login_id": f"hong_{suffix}", "password": "Initial1!"},
        )
        assert login.status_code == 200
    finally:
        if created_id:
            _cleanup_user(db_session, uuid.UUID(created_id))
        _cleanup_user(db_session, admin.id)


def test_update_role_invalidates_sessions(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    target = _create_user(db_session, login_id=f"t_{suffix}", password="Secret123!", role="USER")
    try:
        target_client = TestClient(client.app)
        assert (
            target_client.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "Secret123!"},
            ).status_code
            == 200
        )
        assert target_client.get("/api/v1/auth/me").status_code == 200

        csrf = _login(client, admin.login_id)
        patched = client.patch(
            f"/api/v1/users/{target.id}",
            headers={"X-CSRF-Token": csrf},
            json={"role": "ADMIN"},
        )
        assert patched.status_code == 200
        assert patched.json()["data"]["role"] == "ADMIN"
        # Existing session for target must be gone
        assert target_client.get("/api/v1/auth/me").status_code == 401
    finally:
        _cleanup_user(db_session, target.id)
        _cleanup_user(db_session, admin.id)


def test_update_inactive_invalidates_sessions(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    target = _create_user(db_session, login_id=f"t_{suffix}", password="Secret123!", role="USER")
    try:
        target_client = TestClient(client.app)
        assert (
            target_client.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "Secret123!"},
            ).status_code
            == 200
        )
        csrf = _login(client, admin.login_id)
        patched = client.patch(
            f"/api/v1/users/{target.id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "INACTIVE"},
        )
        assert patched.status_code == 200
        assert target_client.get("/api/v1/auth/me").status_code == 401
    finally:
        _cleanup_user(db_session, target.id)
        _cleanup_user(db_session, admin.id)


def test_name_only_update_keeps_session(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    target = _create_user(db_session, login_id=f"t_{suffix}", password="Secret123!", role="USER")
    try:
        target_client = TestClient(client.app)
        assert (
            target_client.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "Secret123!"},
            ).status_code
            == 200
        )
        csrf = _login(client, admin.login_id)
        patched = client.patch(
            f"/api/v1/users/{target.id}",
            headers={"X-CSRF-Token": csrf},
            json={"name": "새이름", "department": "AI연구팀"},
        )
        assert patched.status_code == 200
        assert target_client.get("/api/v1/auth/me").status_code == 200
        assert target_client.get("/api/v1/auth/me").json()["data"]["name"] == "새이름"
    finally:
        _cleanup_user(db_session, target.id)
        _cleanup_user(db_session, admin.id)


def test_cannot_deactivate_self(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    # Ensure another admin exists so LAST_ACTIVE_ADMIN is not the only blocker.
    other = _create_user(db_session, login_id=f"a2_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        response = client.patch(
            f"/api/v1/users/{admin.id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "INACTIVE"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "CANNOT_DEACTIVATE_SELF"
    finally:
        _cleanup_user(db_session, other.id)
        _cleanup_user(db_session, admin.id)


def test_last_active_admin_protection(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    # Isolate: only this admin is ACTIVE ADMIN among our created users;
    # there may be other admins in DB from e2e. Count-based protection uses
    # global count, so create a dedicated scenario by demoting extras is hard.
    # Instead: create one admin, and if global count is 1 for our purpose we
    # check the endpoint. If other ACTIVE ADMINs exist in DB, temporarily
    # mark them inactive in the transaction scope of this test.
    from app.db.models.user import AppUser

    existing_admins = list(
        db_session.execute(
            select(AppUser).where(AppUser.role == "ADMIN", AppUser.status == "ACTIVE")
        ).scalars()
    )
    paused = []
    for row in existing_admins:
        row.status = "INACTIVE"
        paused.append(row.id)
        db_session.add(row)
    db_session.commit()

    admin = _create_user(db_session, login_id=f"solo_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        demote = client.patch(
            f"/api/v1/users/{admin.id}",
            headers={"X-CSRF-Token": csrf},
            json={"role": "USER"},
        )
        assert demote.status_code == 400
        assert demote.json()["code"] == "LAST_ACTIVE_ADMIN_REQUIRED"

        # Self-deactivate blocked first by CANNOT_DEACTIVATE_SELF when only self,
        # but also LAST_ACTIVE_ADMIN — either is acceptable; self check runs first.
        inactive = client.patch(
            f"/api/v1/users/{admin.id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "INACTIVE"},
        )
        assert inactive.status_code == 400
        assert inactive.json()["code"] in {
            "CANNOT_DEACTIVATE_SELF",
            "LAST_ACTIVE_ADMIN_REQUIRED",
        }
    finally:
        _cleanup_user(db_session, admin.id)
        for uid in paused:
            row = db_session.get(AppUser, uid)
            if row is not None:
                row.status = "ACTIVE"
                db_session.add(row)
        db_session.commit()


def test_reset_password_invalidates_and_changes_hash(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    target = _create_user(db_session, login_id=f"t_{suffix}", password="OldPass12!", role="USER")
    try:
        target_client = TestClient(client.app)
        assert (
            target_client.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "OldPass12!"},
            ).status_code
            == 200
        )
        csrf = _login(client, admin.login_id)
        reset = client.post(
            f"/api/v1/users/{target.id}/reset-password",
            headers={"X-CSRF-Token": csrf},
            json={"new_password": "NewPass99!"},
        )
        assert reset.status_code == 204
        assert target_client.get("/api/v1/auth/me").status_code == 401

        assert (
            client.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "OldPass12!"},
            ).status_code
            == 401
        )
        # Use fresh client cookies
        fresh = TestClient(client.app)
        assert (
            fresh.post(
                "/api/v1/auth/login",
                json={"login_id": target.login_id, "password": "NewPass99!"},
            ).status_code
            == 200
        )

        from app.db.models.revision import AuditLog

        audits = list(
            db_session.execute(
                select(AuditLog).where(
                    AuditLog.action_type == "USER_RESET_PASSWORD",
                    AuditLog.target_id == target.id,
                )
            ).scalars()
        )
        assert audits
        for entry in audits:
            blob = str(entry.before_json) + str(entry.after_json) + str(entry.metadata_json)
            assert "OldPass12!" not in blob
            assert "NewPass99!" not in blob
            assert "password_hash" not in blob
    finally:
        _cleanup_user(db_session, target.id)
        _cleanup_user(db_session, admin.id)


def test_create_user_requires_csrf(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        _login(client, admin.login_id)
        response = client.post(
            "/api/v1/users",
            json={
                "login_id": f"x_{suffix}",
                "name": "X",
                "role": "USER",
                "password": "Secret123!",
            },
        )
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
    finally:
        _cleanup_user(db_session, admin.id)
