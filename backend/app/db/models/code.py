"""code_master / code_alias models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CodeMaster(Base):
    __tablename__ = "code_master"
    __table_args__ = (
        CheckConstraint(
            "code_type IN ('JOB', 'TECH', 'EXP', 'BIZ', 'CUSTOMER_TYPE', 'DOC_TYPE')",
            name="code_master_code_type_check",
        ),
        Index("idx_code_master_type_parent", "code_type", "parent_code", "sort_order"),
    )

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    code_type: Mapped[str] = mapped_column(String(30), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CodeAlias(Base):
    __tablename__ = "code_alias"
    __table_args__ = (
        UniqueConstraint("code", "normalized_alias", name="code_alias_code_normalized_alias_key"),
        # idx_code_alias_normalized: GIN (normalized_alias gin_trgm_ops) — Alembic/migration-only
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(300), nullable=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
