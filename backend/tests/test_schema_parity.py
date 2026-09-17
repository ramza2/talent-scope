"""Schema parity smoke tests against a migrated PostgreSQL database.

These tests verify Alembic-applied DDL and required reference data (not just
SQLAlchemy Metadata counts). They skip when DATABASE_URL is unreachable.
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

EXPECTED_DOC_TYPES = {
    "DOC-RESUME",
    "DOC-PROFILE",
    "DOC-CAREER",
    "DOC-KOSA",
    "DOC-CERT",
    "DOC-PORTFOLIO",
    "DOC-EDU",
    "DOC-OTHER",
}

EXPECTED_REFERENCE_CODES = {
    # JOB roots / leaves
    "JOB-MGT",
    "JOB-MGT-PM",
    "JOB-MGT-PL",
    "JOB-MGT-PMO",
    "JOB-ARC",
    "JOB-ARC-SA",
    "JOB-ARC-AA",
    "JOB-ARC-TA",
    "JOB-DATA",
    "JOB-DATA-DA",
    "JOB-DATA-DBA",
    "JOB-DEV",
    "JOB-DEV-GEN",
    "JOB-DEV-BE",
    "JOB-DEV-FE",
    "JOB-DEV-FS",
    "JOB-DEV-MOB",
    "JOB-DEV-INT",
    "JOB-AI",
    "JOB-AI-DEV",
    "JOB-AI-ML",
    "JOB-AI-LLM",
    "JOB-AI-VISION",
    "JOB-AI-PLATFORM",
    "JOB-SYS",
    "JOB-SYS-SE",
    "JOB-SYS-OS",
    "JOB-SYS-CLOUD",
    "JOB-SYS-MW",
    "JOB-NET-ENG",
    "JOB-SEC-ENG",
    "JOB-OPS-SYS",
    "JOB-OPS-APP",
    "JOB-QA-ENG",
    # TECH / EXP roots plus canonical leaves already fixed by docs/API/tests
    "TECH-LANG",
    "TECH-LANG-PYTHON",
    "TECH-BE",
    "TECH-FE",
    "TECH-DB",
    "TECH-DB-ORACLE",
    "TECH-AI",
    "TECH-INFRA",
    "TECH-DATA",
    "EXP-AI",
    "EXP-AI-RAG",
    "EXP-SW",
    "EXP-DATA",
    "EXP-DATA-DB-TUNING",
    "EXP-INFRA",
    "EXP-MGT",
    # BIZ roots
    "BIZ-PUBLIC",
    "BIZ-DEFENSE",
    "BIZ-HEALTHCARE",
    "BIZ-FINANCE",
    "BIZ-MANUFACTURING",
    "BIZ-ENERGY",
    "BIZ-TELECOM",
    "BIZ-RETAIL",
    "BIZ-LOGISTICS",
    "BIZ-EDUCATION",
    "BIZ-TRANSPORT",
    "BIZ-CONSTRUCTION",
    "BIZ-MEDIA",
    "BIZ-ENTERPRISE",
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
    # Focus on checks we know exist in schema.sql.
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


def test_default_reference_codes_seeded(db_engine) -> None:
    expected = EXPECTED_DOC_TYPES | EXPECTED_REFERENCE_CODES
    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT code, code_type, is_active "
                "FROM code_master WHERE code = ANY(:codes)"
            ),
            {"codes": sorted(expected)},
        ).fetchall()

    by_code = {
        code: {"type": code_type, "active": is_active}
        for code, code_type, is_active in rows
    }
    missing = expected - set(by_code)
    assert not missing, f"missing default codes: {sorted(missing)}"
    assert all(by_code[code]["type"] == "DOC_TYPE" for code in EXPECTED_DOC_TYPES)
    assert all(by_code[code]["active"] for code in expected)


def test_default_code_seed_migration_is_idempotent() -> None:
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0002_seed_default_codes.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'down_revision = "0001_initial_schema"' in source
    assert "ON CONFLICT (code) DO NOTHING" in source
    assert '"DOC-RESUME"' in source
    assert '"JOB-AI-DEV"' in source
    assert '"TECH-LANG-PYTHON"' in source
    assert '"EXP-AI-RAG"' in source
    assert '"EXP-DATA-DB-TUNING"' in source
    assert '"BIZ-PUBLIC"' in source
    # Fresh installs get the intended hierarchy, while reused DB rows are not overwritten.
    assert '"parent_code": "JOB-AI"' in source
    assert '"parent_code": "TECH-LANG"' in source
    assert '"parent_code": "TECH-DB"' in source
    assert '"parent_code": "EXP-AI"' in source
    assert '"parent_code": "EXP-DATA"' in source


def test_migration_module_is_self_contained() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial_schema.py"
    source = path.read_text(encoding="utf-8")
    assert "BASELINE_DDL" in source
    assert "read_text(" not in source
    assert "_schema_sql_path" not in source
    assert "CREATE TABLE app_user" in source
    assert "idx_search_index_embedding_hnsw" in source
