"""Seed minimal security EXP/TECH taxonomy and aliases.

Revision ID: 0003_seed_security_taxonomy
Revises: 0002_seed_default_codes
Create Date: 2026-10-01

Adds Security roots/leaves so profile extraction can classify:
- EXP: broad security expertise, security operations, security build
- TECH: concrete products AD / NAC / SEP only when present in source

Existing rows are never overwritten (ON CONFLICT DO NOTHING). Operators may
edit seeded rows later without a future deploy resetting those changes.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from app.modules.codes.normalize import prepare_aliases

revision = "0003_seed_security_taxonomy"
down_revision = "0002_seed_default_codes"
branch_labels = None
depends_on = None


ROOT_CODES = (
    # After existing EXP roots (EXP-MGT sort_order=500).
    {
        "code": "EXP-SEC",
        "code_type": "EXP",
        "name": "Security",
        "parent_code": None,
        "sort_order": 600,
    },
    # After existing TECH roots (TECH-DATA sort_order=700).
    {
        "code": "TECH-SEC",
        "code_type": "TECH",
        "name": "Security",
        "parent_code": None,
        "sort_order": 800,
    },
)

CHILD_CODES = (
    {
        "code": "EXP-SEC-OPS",
        "code_type": "EXP",
        "name": "정보보안 운영",
        "parent_code": "EXP-SEC",
        "sort_order": 610,
    },
    {
        "code": "EXP-SEC-BUILD",
        "code_type": "EXP",
        "name": "정보보안 구축",
        "parent_code": "EXP-SEC",
        "sort_order": 620,
    },
    {
        "code": "TECH-SEC-AD",
        "code_type": "TECH",
        "name": "Active Directory",
        "parent_code": "TECH-SEC",
        "sort_order": 810,
    },
    {
        "code": "TECH-SEC-NAC",
        "code_type": "TECH",
        "name": "NAC",
        "parent_code": "TECH-SEC",
        "sort_order": 820,
    },
    {
        "code": "TECH-SEC-SEP",
        "code_type": "TECH",
        "name": "Symantec Endpoint Protection",
        "parent_code": "TECH-SEC",
        "sort_order": 830,
    },
)

# Aliases that normalize equal to the standard name are skipped by prepare_aliases
# (name matching still resolves via code_master.name).
CODE_ALIASES: dict[str, tuple[str, ...]] = {
    "EXP-SEC": ("보안", "정보보안", "정보보호"),
    "EXP-SEC-OPS": (
        "정보보안 운영",
        "보안 운영",
        "보안시스템 운영",
        "정보보안시스템 운영",
    ),
    "EXP-SEC-BUILD": (
        "정보보안 구축",
        "보안 구축",
        "보안시스템 구축",
        "정보보호 강화",
    ),
    "TECH-SEC-AD": ("AD", "Active Directory", "액티브 디렉터리"),
    "TECH-SEC-NAC": ("NAC", "Network Access Control"),
    "TECH-SEC-SEP": ("SEP", "Symantec Endpoint Protection"),
}

_INSERT_CODE_SQL = text(
    """
    INSERT INTO code_master (
        code,
        code_type,
        parent_code,
        name,
        sort_order,
        is_active
    ) VALUES (
        :code,
        :code_type,
        :parent_code,
        :name,
        :sort_order,
        TRUE
    )
    ON CONFLICT (code) DO NOTHING
    """
)

_INSERT_ALIAS_SQL = text(
    """
    INSERT INTO code_alias (
        id,
        code,
        alias,
        normalized_alias
    ) VALUES (
        gen_random_uuid(),
        :code,
        :alias,
        :normalized_alias
    )
    ON CONFLICT (code, normalized_alias) DO NOTHING
    """
)


def _alias_rows() -> list[dict[str, str]]:
    name_by_code = {
        row["code"]: row["name"] for row in (*ROOT_CODES, *CHILD_CODES)
    }
    out: list[dict[str, str]] = []
    for code, aliases in CODE_ALIASES.items():
        for alias, normalized in prepare_aliases(
            list(aliases),
            standard_name=name_by_code.get(code),
        ):
            out.append(
                {
                    "code": code,
                    "alias": alias,
                    "normalized_alias": normalized,
                }
            )
    return out


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(_INSERT_CODE_SQL, list(ROOT_CODES))
    bind.execute(_INSERT_CODE_SQL, list(CHILD_CODES))
    aliases = _alias_rows()
    if aliases:
        bind.execute(_INSERT_ALIAS_SQL, aliases)


def downgrade() -> None:
    # Reference data is intentionally retained on downgrade. Removing a seeded
    # code/alias can be destructive once it is referenced by profiles/documents,
    # and re-upgrade is safe because upgrade() uses ON CONFLICT DO NOTHING.
    pass
