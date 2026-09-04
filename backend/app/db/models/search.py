"""Search index models — matches db/schema.sql (embedding VECTOR(1024))."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Computed,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBEDDING_DIMENSIONS = 1024


class SearchIndexItem(Base):
    __tablename__ = "search_index_item"
    __table_args__ = (
        Index("idx_search_index_person_type", "person_id", "object_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(30), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', COALESCE(search_text, ''))", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    source_weight: Mapped[Decimal] = mapped_column(
        Numeric(6, 3), nullable=False, server_default="1.000"
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchIndexJob(Base):
    __tablename__ = "search_index_job"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="search_index_job_idempotency_key_key"),
        Index("idx_search_index_job_status_created", "status", "created_at"),
        Index("idx_search_index_job_person", "person_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=True
    )
    object_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="PENDING")
    idempotency_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
