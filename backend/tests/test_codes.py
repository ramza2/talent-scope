"""Codes API tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

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
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _cleanup_codes(db_session, codes: list[str]) -> None:
    from app.db.models.code import CodeAlias, CodeMaster
    from app.db.models.revision import AuditLog
    from sqlalchemy import select, text

    if not codes:
        return
    db_session.execute(delete(CodeAlias).where(CodeAlias.code.in_(codes)))
    # Delete leaf-first to satisfy parent_code RESTRICT.
    remaining = set(codes)
    for _ in range(len(remaining) + 2):
        if not remaining:
            break
        progress = False
        for code in list(remaining):
            child = db_session.execute(
                select(CodeMaster.code).where(CodeMaster.parent_code == code).limit(1)
            ).scalar_one_or_none()
            if child is not None and child in remaining:
                continue
            if child is not None:
                continue
            db_session.execute(delete(CodeMaster).where(CodeMaster.code == code))
            remaining.discard(code)
            progress = True
        if not progress:
            # Clear parent links among remaining then delete.
            db_session.execute(
                text(
                    "UPDATE code_master SET parent_code = NULL WHERE code = ANY(:codes)"
                ),
                {"codes": list(remaining)},
            )
            db_session.execute(delete(CodeMaster).where(CodeMaster.code.in_(list(remaining))))
            remaining.clear()
            break
    db_session.execute(delete(AuditLog).where(AuditLog.target_type == "CODE"))
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


def test_list_codes_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/codes")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_list_codes_user_and_admin(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    codes = [f"TECH-T-{suffix}", f"TECH-C-{suffix}"]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        create = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={
                "code": codes[0],
                "type": "TECH",
                "name": "Python",
                "aliases": ["파이썬"],
                "sort_order": 10,
            },
        )
        assert create.status_code == 201, create.text

        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        _login(client, user.login_id)
        listed = client.get("/api/v1/codes", params={"type": "TECH", "q": "python"})
        assert listed.status_code == 200
        data = listed.json()["data"]
        assert any(item["code"] == codes[0] for item in data)
        assert any("파이썬" in item["aliases"] for item in data if item["code"] == codes[0])

        by_alias = client.get("/api/v1/codes", params={"q": "파이썬"})
        assert by_alias.status_code == 200
        assert any(item["code"] == codes[0] for item in by_alias.json()["data"])
    finally:
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, user.id)
        _cleanup_user(db_session, admin.id)


def test_create_code_forbidden_for_user(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    try:
        csrf = _login(client, user.login_id)
        response = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": f"TECH-X-{suffix}", "type": "TECH", "name": "X"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"
    finally:
        _cleanup_user(db_session, user.id)


def test_create_code_requires_csrf(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        _login(client, admin.login_id)
        response = client.post(
            "/api/v1/codes",
            json={"code": f"TECH-X-{suffix}", "type": "TECH", "name": "X"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
    finally:
        _cleanup_user(db_session, admin.id)


def test_create_code_validations(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    parent = f"TECH-P-{suffix}"
    child = f"TECH-C-{suffix}"
    codes = [parent, child, f"JOB-X-{suffix}"]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        ok = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": parent, "type": "TECH", "name": "Parent"},
        )
        assert ok.status_code == 201

        dup = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": parent, "type": "TECH", "name": "Dup"},
        )
        assert dup.status_code == 409
        assert dup.json()["code"] == "CODE_ALREADY_EXISTS"

        bad_type = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": f"BAD-{suffix}", "type": "SKILL", "name": "Bad"},
        )
        assert bad_type.status_code == 422  # pydantic validation

        missing_parent = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={
                "code": child,
                "type": "TECH",
                "name": "Child",
                "parent_code": "TECH-MISSING",
            },
        )
        assert missing_parent.status_code == 400
        assert missing_parent.json()["code"] == "INVALID_CODE_PARENT"

        # Create JOB root then try TECH child under JOB parent
        job = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": codes[2], "type": "JOB", "name": "Job"},
        )
        assert job.status_code == 201
        mismatch = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={
                "code": child,
                "type": "TECH",
                "name": "Child",
                "parent_code": codes[2],
            },
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["code"] == "INVALID_CODE_PARENT"
    finally:
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_hierarchy_cycle_and_self_parent(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    a, b, c = f"TECH-A-{suffix}", f"TECH-B-{suffix}", f"TECH-C-{suffix}"
    codes = [a, b, c]
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        for code, name in [(a, "A"), (b, "B"), (c, "C")]:
            resp = client.post(
                "/api/v1/codes",
                headers={"X-CSRF-Token": csrf},
                json={"code": code, "type": "TECH", "name": name},
            )
            assert resp.status_code == 201

        assert (
            client.patch(
                f"/api/v1/codes/{b}",
                headers={"X-CSRF-Token": csrf},
                json={"parent_code": a},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                f"/api/v1/codes/{c}",
                headers={"X-CSRF-Token": csrf},
                json={"parent_code": b},
            ).status_code
            == 200
        )

        self_parent = client.patch(
            f"/api/v1/codes/{a}",
            headers={"X-CSRF-Token": csrf},
            json={"parent_code": a},
        )
        assert self_parent.status_code == 400
        assert self_parent.json()["code"] == "INVALID_CODE_PARENT"

        cycle = client.patch(
            f"/api/v1/codes/{a}",
            headers={"X-CSRF-Token": csrf},
            json={"parent_code": c},
        )
        assert cycle.status_code == 400
        assert cycle.json()["code"] == "CODE_HIERARCHY_CYCLE"
    finally:
        _cleanup_codes(db_session, codes)
        _cleanup_user(db_session, admin.id)


def test_alias_replace_normalize(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    code = f"EXP-RAG-{suffix}"
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    try:
        csrf = _login(client, admin.login_id)
        created = client.post(
            "/api/v1/codes",
            headers={"X-CSRF-Token": csrf},
            json={"code": code, "type": "EXP", "name": "RAG", "aliases": ["검색증강"]},
        )
        assert created.status_code == 201

        replaced = client.put(
            f"/api/v1/codes/{code}/aliases",
            headers={"X-CSRF-Token": csrf},
            json={
                "aliases": [
                    "  Retrieval Augmented Generation  ",
                    "검색증강생성",
                    "검색증강생성",
                    "RAG",
                    "",
                    "   ",
                ]
            },
        )
        assert replaced.status_code == 200, replaced.text
        aliases = replaced.json()["data"]["aliases"]
        assert "Retrieval Augmented Generation" in aliases
        assert "검색증강생성" in aliases
        assert aliases.count("검색증강생성") == 1
        # Standard name matching alias dropped
        assert "RAG" not in aliases

        detail = client.get(f"/api/v1/codes/{code}")
        assert detail.status_code == 200
        assert detail.json()["data"]["type"] == "EXP"
    finally:
        _cleanup_codes(db_session, [code])
        _cleanup_user(db_session, admin.id)


def test_get_code_not_found(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    try:
        _login(client, user.login_id)
        response = client.get("/api/v1/codes/NO-SUCH-CODE")
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
    finally:
        _cleanup_user(db_session, user.id)


def test_normalize_alias_unit() -> None:
    from app.modules.codes.normalize import normalize_alias, prepare_aliases

    assert normalize_alias("  Foo   Bar ") == "foo bar"
    pairs = prepare_aliases(
        ["Python", " python ", "파이썬", "Python"],
        standard_name="Python",
    )
    assert pairs == [("파이썬", "파이썬")]
