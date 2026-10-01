"""Security EXP/TECH taxonomy seed (alembic 0003) regression tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)


EXPECTED_CODES = {
    "EXP-SEC": ("EXP", None),
    "EXP-SEC-OPS": ("EXP", "EXP-SEC"),
    "EXP-SEC-BUILD": ("EXP", "EXP-SEC"),
    "TECH-SEC": ("TECH", None),
    "TECH-SEC-AD": ("TECH", "TECH-SEC"),
    "TECH-SEC-NAC": ("TECH", "TECH-SEC"),
    "TECH-SEC-SEP": ("TECH", "TECH-SEC"),
}

EXPECTED_ALIASES = {
    "정보보안": "EXP-SEC",
    "정보보안 운영": "EXP-SEC-OPS",  # may resolve via name; alias optional
    "정보보안시스템 운영": "EXP-SEC-OPS",
    "정보보호 강화": "EXP-SEC-BUILD",
    "AD": "TECH-SEC-AD",
    "NAC": "TECH-SEC-NAC",
    "SEP": "TECH-SEC-SEP",
}


@pytest.fixture(scope="module")
def db_engine():
    from alembic import command
    from alembic.config import Config

    url = os.environ["DATABASE_URL"]
    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    # Ensure 0003 is applied on the shared test DB (idempotent).
    command.upgrade(cfg, "head")
    # Second upgrade must remain a no-op for seed rows.
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_security_taxonomy_migration_source_idempotent() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0003_seed_security_taxonomy.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'revision = "0003_seed_security_taxonomy"' in source
    assert 'down_revision = "0002_seed_default_codes"' in source
    assert "ON CONFLICT (code) DO NOTHING" in source
    assert "ON CONFLICT (code, normalized_alias) DO NOTHING" in source
    assert "intentionally retained on downgrade" in source
    assert '"EXP-SEC"' in source
    assert '"EXP-SEC-OPS"' in source
    assert '"EXP-SEC-BUILD"' in source
    assert '"TECH-SEC"' in source
    assert '"TECH-SEC-AD"' in source
    assert '"TECH-SEC-NAC"' in source
    assert '"TECH-SEC-SEP"' in source
    assert "_prepare_aliases" in source
    assert "_normalize_alias" in source
    assert "from app.modules" not in source
    assert "app.modules.codes.normalize" not in source


def test_security_taxonomy_codes_and_aliases_seeded(db_engine) -> None:
    from app.modules.codes.normalize import normalize_alias

    with db_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT code, code_type, parent_code
                FROM code_master
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": sorted(EXPECTED_CODES)},
        ).fetchall()
        by_code = {code: (code_type, parent) for code, code_type, parent in rows}
        assert set(by_code) == set(EXPECTED_CODES)
        for code, (expected_type, expected_parent) in EXPECTED_CODES.items():
            code_type, parent = by_code[code]
            assert code_type == expected_type, code
            assert parent == expected_parent, code

        # Name-equal aliases may be omitted by _prepare_aliases; standard name
        # still resolves. Assert required alias rows or name match.
        name_rows = conn.execute(
            text(
                """
                SELECT code, name FROM code_master
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": sorted(EXPECTED_CODES)},
        ).fetchall()
        name_by_code = {code: name for code, name in name_rows}

        alias_rows = conn.execute(
            text(
                """
                SELECT code, normalized_alias
                FROM code_alias
                WHERE code = ANY(:codes)
                """
            ),
            {"codes": sorted(EXPECTED_CODES)},
        ).fetchall()
        alias_to_codes: dict[str, set[str]] = {}
        for code, normalized in alias_rows:
            alias_to_codes.setdefault(normalized, set()).add(code)

        for alias, expected_code in EXPECTED_ALIASES.items():
            norm = normalize_alias(alias)
            via_alias = expected_code in alias_to_codes.get(norm, set())
            via_name = normalize_alias(name_by_code[expected_code]) == norm
            assert via_alias or via_name, (
                f"missing mapping for alias={alias!r} -> {expected_code}"
            )

        # No duplicate (code, normalized_alias) pairs.
        dupes = conn.execute(
            text(
                """
                SELECT code, normalized_alias, COUNT(*)
                FROM code_alias
                WHERE code = ANY(:codes)
                GROUP BY code, normalized_alias
                HAVING COUNT(*) > 1
                """
            ),
            {"codes": sorted(EXPECTED_CODES)},
        ).fetchall()
        assert dupes == []
