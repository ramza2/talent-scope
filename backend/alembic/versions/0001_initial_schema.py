"""Initial TalentScope schema from db/schema.sql.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-04

The operational Source of Truth for the baseline schema is ``db/schema.sql``.
This migration applies that file (extensions, tables, indexes, views, triggers)
so empty databases created via Alembic match the design baseline.

After this revision, further schema changes must be Alembic migrations — do not
re-apply ``schema.sql`` in production.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

REQUIRED_EXTENSIONS = ("pgcrypto", "vector", "pg_trgm")


def _schema_sql_path() -> Path:
    # backend/alembic/versions -> repo root
    return Path(__file__).resolve().parents[3] / "db" / "schema.sql"


def _ensure_extensions(conn) -> None:
    """Create required extensions, or verify they already exist.

    CREATE EXTENSION typically needs a superuser. Cloud Agent / docker images
    often apply extensions as postgres; application roles may lack privilege.
    """
    for ext in REQUIRED_EXTENSIONS:
        try:
            conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
        except Exception as exc:  # noqa: BLE001 - surface clear guidance
            exists = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = :name"),
                {"name": ext},
            ).scalar()
            if exists:
                continue
            raise RuntimeError(
                f"Extension '{ext}' is missing and could not be created "
                f"({exc}). Create it as a PostgreSQL superuser first, e.g. "
                f'CREATE EXTENSION IF NOT EXISTS "{ext}";'
            ) from exc


def upgrade() -> None:
    conn = op.get_bind()

    # Skip if baseline tables already exist (e.g. Cloud Agent applied schema.sql).
    existing = conn.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "AND table_name = 'app_user'"
        )
    ).scalar()
    if existing and int(existing) > 0:
        return

    _ensure_extensions(conn)

    schema_path = _schema_sql_path()
    sql = schema_path.read_text(encoding="utf-8")

    cleaned_lines: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip().upper()
        if stripped in {"BEGIN;", "COMMIT;"}:
            continue
        # Extensions are handled above (may require superuser).
        if stripped.startswith("CREATE EXTENSION"):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)

    raw_conn = conn.connection.dbapi_connection
    raw_conn.execute(cleaned)


def downgrade() -> None:
    # Destructive full drop — only for empty/dev databases.
    conn = op.get_bind()
    conn.execute(
        text(
            """
            DROP VIEW IF EXISTS vw_person_customer_type CASCADE;
            DROP VIEW IF EXISTS vw_person_business_domain CASCADE;
            DROP TABLE IF EXISTS search_index_job CASCADE;
            DROP TABLE IF EXISTS search_index_item CASCADE;
            DROP TABLE IF EXISTS audit_log CASCADE;
            DROP TABLE IF EXISTS profile_revision CASCADE;
            DROP TABLE IF EXISTS evidence_link CASCADE;
            DROP TABLE IF EXISTS analysis_diff_evidence CASCADE;
            DROP TABLE IF EXISTS evidence CASCADE;
            DROP TABLE IF EXISTS analysis_diff_item CASCADE;
            DROP TABLE IF EXISTS analysis_run_document CASCADE;
            DROP TABLE IF EXISTS analysis_run CASCADE;
            DROP TABLE IF EXISTS document_chunk CASCADE;
            DROP TABLE IF EXISTS document_page CASCADE;
            DROP TABLE IF EXISTS document CASCADE;
            DROP TABLE IF EXISTS document_group CASCADE;
            DROP TABLE IF EXISTS project_customer_type CASCADE;
            DROP TABLE IF EXISTS project_business_domain CASCADE;
            DROP TABLE IF EXISTS project_expertise CASCADE;
            DROP TABLE IF EXISTS project_skill CASCADE;
            DROP TABLE IF EXISTS project_job CASCADE;
            DROP TABLE IF EXISTS project CASCADE;
            DROP TABLE IF EXISTS certification CASCADE;
            DROP TABLE IF EXISTS education CASCADE;
            DROP TABLE IF EXISTS employment_history CASCADE;
            DROP TABLE IF EXISTS person_expertise CASCADE;
            DROP TABLE IF EXISTS person_skill CASCADE;
            DROP TABLE IF EXISTS person_job CASCADE;
            DROP TABLE IF EXISTS upload_temp_file CASCADE;
            DROP TABLE IF EXISTS upload_session CASCADE;
            DROP TABLE IF EXISTS person_profile CASCADE;
            DROP TABLE IF EXISTS person CASCADE;
            DROP TABLE IF EXISTS code_alias CASCADE;
            DROP TABLE IF EXISTS code_master CASCADE;
            DROP TABLE IF EXISTS app_user CASCADE;
            DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
            """
        )
    )
