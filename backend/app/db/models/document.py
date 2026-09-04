"""Document models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentGroup(Base):
    __tablename__ = "document_group"
    # idx_document_group_person_type ... WHERE deleted_at IS NULL — Alembic/migration-only

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    document_type_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        CheckConstraint("version_no > 0", name="document_version_no_check"),
        CheckConstraint("file_size >= 0", name="document_file_size_check"),
        CheckConstraint(
            "preview_page_count IS NULL OR preview_page_count >= 0",
            name="document_preview_page_count_check",
        ),
        CheckConstraint(
            "processing_status IN ('UPLOADED', 'PROCESSING', 'READY', 'FAILED')",
            name="document_processing_status_check",
        ),
        UniqueConstraint(
            "document_group_id", "version_no", name="document_document_group_id_version_no_key"
        ),
        Index("idx_document_sha256", "sha256"),
        # Partial / DESC indexes are Alembic/migration-only:
        # uq_document_group_latest, idx_document_group_version, idx_document_processing_status
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_group.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    extension: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="UPLOADED"
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentPage(Base):
    __tablename__ = "document_page"
    __table_args__ = (
        CheckConstraint("page_no > 0", name="document_page_page_no_check"),
        CheckConstraint(
            "extraction_method IS NULL OR extraction_method IN "
            "('TEXT_PARSER', 'VLM', 'OCR', 'HYBRID')",
            name="document_page_extraction_method_check",
        ),
        UniqueConstraint("document_id", "page_no", name="document_page_document_id_page_no_key"),
        Index("idx_document_page_document", "document_id", "page_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunk"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="document_chunk_chunk_index_check"),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="document_chunk_token_count_check",
        ),
        CheckConstraint(
            "page_to IS NULL OR page_from IS NULL OR page_to >= page_from",
            name="document_chunk_check",
        ),
        UniqueConstraint(
            "document_id", "chunk_index", name="document_chunk_document_id_chunk_index_key"
        ),
        Index("idx_document_chunk_document", "document_id", "chunk_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
