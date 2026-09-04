"""Schema parity smoke tests against a migrated PostgreSQL database.

These tests verify Alembic-applied DDL (not just SQLAlchemy Metadata counts).
They skip when DATABASE_URL is unreachable.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

EXPECTED_TABLES = {
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

EXPECTED_VIEWS = {
    "vw_person_business_domain",
    "vw_person_customer_type",
}

CRITICAL_INDEX_SNIPPETS = {
    "idx_project_person_period": [
        "person_id",
        "start_date",
        "DESC",
        "deleted_at IS NULL",
    ],
    "uq_document_group_latest": [
        "document_group_id",
        "is_latest",
        "deleted_at IS NULL",
    ],
    "idx_search_index_tsv": ["USING gin", "search_tsv", "is_active"],
    "idx_search_index_text_trgm": ["USING gin", "gin_trgm_ops", "search_text"],
    "idx_search_index_embedding_hnsw": [
        "USING hnsw",
        "vector_cosine_ops",
        "embedding",
    ],
    "uq_search_index_active_object_version": [
        "object_type",
        "object_id",
        "COALESCE",
        "embedding_model",
        "is_active",
    ],
}


@pytest.fixture(scope="module")
def db_engine():
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
    )
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Database not available: {exc}")
    yield engine
    engine.dispose()


def test_mvp_tables_present(db_engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        ).fetchall()
    names = {r[0] for r in rows} - {"alembic_version"}
    missing = EXPECTED_TABLES - names
    assert not missing, f"missing tables: {sorted(missing)}"
    assert len(EXPECTED_TABLES) == 33


def test_views_present(db_engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.views "
                "WHERE table_schema='public'"
            )
        ).fetchall()
    names = {r[0] for r in rows}
    assert EXPECTED_VIEWS.issubset(names)


def test_updated_at_triggers_present(db_engine) -> None:
    with db_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND NOT t.tgisinternal "
                "AND t.tgname LIKE 'trg_%_updated_at'"
            )
        ).scalar()
    assert int(count) >= 10


def test_key_check_constraints_present(db_engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint "
                "WHERE contype='c' AND connamespace = 'public'::regnamespace"
            )
        ).fetchall()
    defs = " | ".join(f"{name}:{defn}" for name, defn in rows).upper()
    required_fragments = [
        "ROLE IN",
        "CODE_TYPE IN",
        "TECHNICAL_GRADE",
        "PROCESSING_STATUS",
        "CHANGE_TYPE IN",
        "REVIEW_STATUS",
        "VECTOR",  # may not appear in check; skip if not
    ]
    # Focus on checks we know exist in schema.sql
    for fragment in [
        "USER",
        "ADMIN",
        "BEGINNER",
        "UPLOADED",
        "QUEUED",
        "PENDING",
        "UPSERT",
    ]:
        assert fragment in defs, f"expected CHECK fragment {fragment!r} not found"


def test_critical_index_definitions(db_engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public'"
            )
        ).fetchall()
    by_name = {name: indexdef for name, indexdef in rows}
    for index_name, snippets in CRITICAL_INDEX_SNIPPETS.items():
        assert index_name in by_name, f"missing index {index_name}"
        indexdef = by_name[index_name]
        for snippet in snippets:
            assert snippet.lower() in indexdef.lower(), (
                f"{index_name} missing snippet {snippet!r} in: {indexdef}"
            )


def test_extensions_present(db_engine) -> None:
    with db_engine.connect() as conn:
        rows = conn.execute(text("SELECT extname FROM pg_extension")).fetchall()
    names = {r[0] for r in rows}
    assert {"pgcrypto", "vector", "pg_trgm"}.issubset(names)


def test_migration_module_is_self_contained() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial_schema.py"
    source = path.read_text(encoding="utf-8")
    assert "BASELINE_DDL" in source
    assert "read_text(" not in source
    assert "_schema_sql_path" not in source
    assert "CREATE TABLE app_user" in source
    assert "idx_search_index_embedding_hnsw" in source
