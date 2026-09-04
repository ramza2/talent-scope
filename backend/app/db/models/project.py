"""Project and project link models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="project_check",
        ),
        CheckConstraint(
            "duration_months IS NULL OR duration_months >= 0",
            name="project_duration_months_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="project_source_type_check",
        ),
        # Complex indexes (DESC / partial WHERE / GIN trgm) are Alembic/migration-only:
        # idx_project_person_period, idx_project_name_trgm, idx_project_customer_trgm
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    project_name: Mapped[str] = mapped_column(String(500), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    source_analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_run.id", ondelete="SET NULL", name="fk_project_source_analysis_run"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectJob(Base):
    __tablename__ = "project_job"
    __table_args__ = (Index("idx_project_job_code", "job_code", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    job_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), primary_key=True
    )


class ProjectSkill(Base):
    __tablename__ = "project_skill"
    __table_args__ = (Index("idx_project_skill_code", "tech_code", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    tech_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), primary_key=True
    )


class ProjectExpertise(Base):
    __tablename__ = "project_expertise"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('EXPLICIT', 'INFERRED')",
            name="project_expertise_evidence_type_check",
        ),
        Index("idx_project_expertise_code", "exp_code", "project_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    exp_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), primary_key=True
    )
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="EXPLICIT")


class ProjectBusinessDomain(Base):
    __tablename__ = "project_business_domain"
    __table_args__ = (Index("idx_project_biz_code", "biz_code", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    biz_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), primary_key=True
    )


class ProjectCustomerType(Base):
    __tablename__ = "project_customer_type"
    __table_args__ = (Index("idx_project_customer_type_code", "customer_type_code", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
    )
    customer_type_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), primary_key=True
    )
