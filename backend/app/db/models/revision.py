"""Profile revision and audit log models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfileRevision(Base):
    __tablename__ = "profile_revision"
    __table_args__ = (
        UniqueConstraint("person_id", "revision_no", name="profile_revision_person_id_revision_no_key"),
        Index("idx_profile_revision_person", "person_id", "revision_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_run.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_log_target", "target_type", "target_id", "created_at"),
        Index("idx_audit_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
