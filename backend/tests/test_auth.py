"""Auth / session / CSRF / RBAC tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

# Configure env before app import side effects in fixtures.
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

    # Cleanup only keys created under this test prefix.
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

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def test_password_hash_and_verify() -> None:
    from app.core.security import hash_password, verify_password

    hashed = hash_password("correct-horse")
    assert hashed != "correct-horse"
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong", hashed) is False
    assert verify_password("anything", None) is False


def test_login_admin_success(client: TestClient, db_session) -> None:
    login_id = f"admin_{uuid.uuid4().hex[:8]}"
    user = _create_user(
        db_session, login_id=login_id, password="Secret123!", role="ADMIN", name="관리자"
    )
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"login_id": login_id, "password": "Secret123!"},
        )
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["login_id"] == login_id
        assert body["role"] == "ADMIN"
        assert client.cookies.get("ts_session")
        assert client.cookies.get("ts_csrf")
    finally:
        _cleanup_user(db_session, user.id)


def test_login_user_success(client: TestClient, db_session) -> None:
    login_id = f"user_{uuid.uuid4().hex[:8]}"
    user = _create_user(db_session, login_id=login_id, password="Secret123!", role="USER")
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"login_id": login_id, "password": "Secret123!"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["role"] == "USER"
    finally:
        _cleanup_user(db_session, user.id)


@pytest.mark.parametrize(
    "case",
    ["wrong_password", "missing_user", "inactive"],
)
def test_login_failures_are_uniform(client: TestClient, db_session, case: str) -> None:
    login_id = f"fail_{uuid.uuid4().hex[:8]}"
    user = None
    password = "Secret123!"
    if case != "missing_user":
        status = "INACTIVE" if case == "inactive" else "ACTIVE"
        user = _create_user(
            db_session, login_id=login_id, password=password, status=status
        )
    try:
        bad_password = "nope" if case == "wrong_password" else password
        target_login = login_id if case != "missing_user" else f"missing_{uuid.uuid4().hex[:8]}"
        response = client.post(
            "/api/v1/auth/login",
            json={"login_id": target_login, "password": bad_password},
        )
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "INVALID_CREDENTIALS"
    finally:
        if user is not None:
            _cleanup_user(db_session, user.id)


def test_me_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_me_and_role_change_reflects_db(client: TestClient, db_session) -> None:
    login_id = f"role_{uuid.uuid4().hex[:8]}"
    user = _create_user(
        db_session, login_id=login_id, password="Secret123!", role="ADMIN"
    )
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_id": login_id, "password": "Secret123!"},
        )
        assert login.status_code == 200

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["data"]["role"] == "ADMIN"

        user.role = "USER"
        db_session.add(user)
        db_session.commit()

        me2 = client.get("/api/v1/auth/me")
        assert me2.status_code == 200
        assert me2.json()["data"]["role"] == "USER"
    finally:
        _cleanup_user(db_session, user.id)


def test_inactive_user_session_invalidated(client: TestClient, db_session) -> None:
    login_id = f"inact_{uuid.uuid4().hex[:8]}"
    user = _create_user(db_session, login_id=login_id, password="Secret123!")
    try:
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"login_id": login_id, "password": "Secret123!"},
            ).status_code
            == 200
        )
        user.status = "INACTIVE"
        db_session.add(user)
        db_session.commit()
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 401
        assert me.json()["code"] == "AUTH_REQUIRED"
    finally:
        _cleanup_user(db_session, user.id)


def test_logout_csrf_and_session_cleared(client: TestClient, db_session) -> None:
    login_id = f"out_{uuid.uuid4().hex[:8]}"
    user = _create_user(db_session, login_id=login_id, password="Secret123!")
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_id": login_id, "password": "Secret123!"},
        )
        assert login.status_code == 200
        csrf = client.cookies.get("ts_csrf")
        assert csrf

        missing = client.post("/api/v1/auth/logout")
        assert missing.status_code == 403
        assert missing.json()["code"] == "CSRF_INVALID"

        wrong = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "totally-wrong"},
        )
        assert wrong.status_code == 403

        ok = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert ok.status_code == 204

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 401
    finally:
        _cleanup_user(db_session, user.id)


def test_require_admin_rbac(client: TestClient, db_session) -> None:
    from fastapi import Depends

    from app.main import create_app
    from app.modules.auth.dependencies import require_admin

    login_id = f"rbac_{uuid.uuid4().hex[:8]}"
    user = _create_user(
        db_session, login_id=login_id, password="Secret123!", role="USER"
    )
    try:
        # Mount a temporary admin-only route on a fresh app for this test.
        os.environ.setdefault("REDIS_KEY_PREFIX", f"talentscope:test:{uuid.uuid4().hex}")
        from app.core.config import get_settings
        from app.core.redis import get_redis

        get_settings.cache_clear()
        get_redis.cache_clear()
        app = create_app()

        @app.get("/api/v1/_test/admin-only")
        def admin_only(_ctx=Depends(require_admin)):
            return {"ok": True}

        with TestClient(app) as local:
            unauth = local.get("/api/v1/_test/admin-only")
            assert unauth.status_code == 401

            local.post(
                "/api/v1/auth/login",
                json={"login_id": login_id, "password": "Secret123!"},
            )
            forbidden = local.get("/api/v1/_test/admin-only")
            assert forbidden.status_code == 403
            assert forbidden.json()["code"] == "FORBIDDEN"

            user.role = "ADMIN"
            db_session.add(user)
            db_session.commit()
            allowed = local.get("/api/v1/_test/admin-only")
            assert allowed.status_code == 200
    finally:
        _cleanup_user(db_session, user.id)


def test_session_store_unavailable(monkeypatch, client: TestClient, db_session) -> None:
    from app.core.exceptions import SessionStoreUnavailableError
    from app.modules.auth.session_store import SessionStore

    login_id = f"redis_{uuid.uuid4().hex[:8]}"
    user = _create_user(db_session, login_id=login_id, password="Secret123!")
    try:

        def _boom(self, user_id):  # noqa: ANN001
            raise SessionStoreUnavailableError()

        monkeypatch.setattr(SessionStore, "create_session", _boom)
        response = client.post(
            "/api/v1/auth/login",
            json={"login_id": login_id, "password": "Secret123!"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "SESSION_STORE_UNAVAILABLE"
    finally:
        _cleanup_user(db_session, user.id)
