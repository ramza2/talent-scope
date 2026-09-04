"""Backend package unit/smoke tests for the application skeleton."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text


@pytest.fixture(scope="module")
def client() -> TestClient:
    # Ensure settings load from repo .env.example defaults when .env is absent.
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
    )
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
    os.environ.setdefault("APP_SECRET_KEY", "test-secret")

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_app_imports() -> None:
    from app.main import app

    assert app.title == "TalentScope API"


def test_health_live(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_structure(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code in {200, 503}
    body = response.json()
    assert "status" in body
    assert "database" in body
    assert "redis" in body
    assert body["status"] in {"ready", "not_ready"}
    assert body["database"] in {"ok", "error"}
    assert body["redis"] in {"ok", "error"}
    if body["status"] == "ready":
        assert response.status_code == 200
        assert body["database"] == "ok"
        assert body["redis"] == "ok"
    else:
        assert response.status_code == 503
        assert body["database"] == "error" or body["redis"] == "error"


def test_sqlalchemy_metadata_import() -> None:
    from app.db.base import Base
    import app.db.models  # noqa: F401

    table_names = set(Base.metadata.tables.keys())
    expected = {
        "app_user",
        "code_master",
        "code_alias",
        "person",
        "person_profile",
        "person_job",
        "person_skill",
        "person_expertise",
        "employment_history",
        "education",
        "certification",
        "upload_session",
        "upload_temp_file",
        "project",
        "project_job",
        "project_skill",
        "project_expertise",
        "project_business_domain",
        "project_customer_type",
        "document_group",
        "document",
        "document_page",
        "document_chunk",
        "analysis_run",
        "analysis_run_document",
        "analysis_diff_item",
        "analysis_diff_evidence",
        "evidence",
        "evidence_link",
        "profile_revision",
        "audit_log",
        "search_index_item",
        "search_index_job",
    }
    assert expected.issubset(table_names)
    assert len(expected) == 33


def test_db_session_selectable() -> None:
    """Requires a running Postgres matching DATABASE_URL."""
    from app.db.session import SessionLocal, engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Database not available: {exc}")

    with SessionLocal() as session:
        value = session.execute(text("SELECT 1")).scalar()
        assert value == 1

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "app_user" in tables or tables == set()  # empty before migration is ok
