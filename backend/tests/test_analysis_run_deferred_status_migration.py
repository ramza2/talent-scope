"""Alembic 0004 DEFERRED status CHECK upgrade/downgrade."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


def _constraint_def(conn) -> str:
    row = conn.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'analysis_run_status_check'
            """
        )
    ).scalar_one()
    return str(row)


def test_deferred_status_migration_upgrade_downgrade_roundtrip() -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    backend_root = Path(__file__).resolve().parents[1]
    database_url = os.environ["DATABASE_URL"]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)

    engine = create_engine(database_url)
    try:
        command.upgrade(cfg, "0004_analysis_deferred_status")
        with engine.connect() as conn:
            defn = _constraint_def(conn)
            assert "DEFERRED" in defn

        command.downgrade(cfg, "0003_seed_security_taxonomy")
        with engine.connect() as conn:
            defn = _constraint_def(conn)
            assert "DEFERRED" not in defn
            assert "QUEUED" in defn

        command.upgrade(cfg, "0004_analysis_deferred_status")
        with engine.connect() as conn:
            defn = _constraint_def(conn)
            assert "DEFERRED" in defn
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()
