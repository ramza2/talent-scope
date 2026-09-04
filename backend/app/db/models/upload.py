"""Upload session / temp file models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UploadSession(Base):
    __tablename__ = "upload_session"
    __table_args__ = (
        CheckConstraint(
            "status IN ('UPLOADING', 'IDENTIFYING', 'IDENTIFIED', 'RESOLVED', 'CANCELLED', 'EXPIRED')",
            name="upload_session_status_check",
        ),
        # idx_upload_session_status_created (status, created_at DESC) — Alembic/migration-only
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="UPLOADING")
    identified_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    identified_company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    identified_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    identified_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_result_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    resolved_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UploadTempFile(Base):
    __tablename__ = "upload_temp_file"
    __table_args__ = (
        CheckConstraint("file_size >= 0", name="upload_temp_file_file_size_check"),
        CheckConstraint(
            "validation_status IN ('PENDING', 'VALID', 'INVALID', 'ENCRYPTED', 'DUPLICATE')",
            name="upload_temp_file_validation_status_check",
        ),
        Index("idx_upload_temp_session", "upload_session_id"),
        Index("idx_upload_temp_sha256", "sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    upload_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("upload_session.id", ondelete="CASCADE"), nullable=False
    )
    document_type_code: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    temp_storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    extension: Mapped[str | None] = mapped_column(String(30), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="PENDING"
    )
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
