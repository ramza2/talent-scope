"""Add DEFERRED status to analysis_run.

Revision ID: 0004_analysis_deferred_status
Revises: 0003_seed_security_taxonomy
Create Date: 2026-10-07

DEFERRED = auto PROFILE analysis waiting for another active run to finish.
"""

from __future__ import annotations

from alembic import op

revision = "0004_analysis_deferred_status"
down_revision = "0003_seed_security_taxonomy"
branch_labels = None
depends_on = None

_NEW_STATUSES = (
    "DEFERRED",
    "QUEUED",
    "PROCESSING",
    "REVIEWING",
    "CONFIRMED",
    "FAILED",
    "CANCELLED",
)
_OLD_STATUSES = (
    "QUEUED",
    "PROCESSING",
    "REVIEWING",
    "CONFIRMED",
    "FAILED",
    "CANCELLED",
)


def upgrade() -> None:
    op.execute("ALTER TABLE analysis_run DROP CONSTRAINT analysis_run_status_check")
    op.execute(
        "ALTER TABLE analysis_run ADD CONSTRAINT analysis_run_status_check "
        "CHECK (status IN ("
        + ", ".join(f"'{s}'" for s in _NEW_STATUSES)
        + "))"
    )


def downgrade() -> None:
    # Preserve rows: DEFERRED cannot remain under the old CHECK.
    op.execute(
        "UPDATE analysis_run SET status = 'CANCELLED', "
        "error_message = COALESCE(error_message, 'downgraded from DEFERRED'), "
        "updated_at = NOW() "
        "WHERE status = 'DEFERRED'"
    )
    op.execute("ALTER TABLE analysis_run DROP CONSTRAINT analysis_run_status_check")
    op.execute(
        "ALTER TABLE analysis_run ADD CONSTRAINT analysis_run_status_check "
        "CHECK (status IN ("
        + ", ".join(f"'{s}'" for s in _OLD_STATUSES)
        + "))"
    )
