"""Person and confirmed profile related models — matches db/schema.sql."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Person(Base):
    __tablename__ = "person"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'ARCHIVED', 'DELETED')",
            name="person_status_check",
        ),
        Index(
            "idx_person_status",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PersonProfile(Base):
    __tablename__ = "person_profile"
    __table_args__ = (
        CheckConstraint(
            "technical_grade IS NULL OR technical_grade IN "
            "('BEGINNER', 'INTERMEDIATE', 'ADVANCED', 'EXPERT', 'UNKNOWN')",
            name="person_profile_technical_grade_check",
        ),
        CheckConstraint(
            "career_calculated_months IS NULL OR career_calculated_months >= 0",
            name="person_profile_career_calculated_months_check",
        ),
        CheckConstraint(
            "career_confirmed_months IS NULL OR career_confirmed_months >= 0",
            name="person_profile_career_confirmed_months_check",
        ),
        CheckConstraint("profile_version > 0", name="person_profile_profile_version_check"),
        # GIN trgm / expression indexes are Alembic/migration-only:
        # idx_person_profile_name_trgm, idx_person_profile_company_trgm,
        # idx_person_profile_email_lower, idx_person_profile_phone, idx_person_profile_grade
    )

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    birth_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_region: Mapped[str | None] = mapped_column(String(200), nullable=True)
    affiliation_company: Mapped[str | None] = mapped_column(String(300), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    current_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    technical_grade: Mapped[str | None] = mapped_column(String(30), nullable=True)
    career_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    career_calculated_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    career_document_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    career_confirmed_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    profile_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonJob(Base):
    __tablename__ = "person_job"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('PRIMARY', 'SECONDARY', 'EXPERIENCE')",
            name="person_job_job_type_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="person_job_source_type_check",
        ),
        UniqueConstraint(
            "person_id", "job_code", "job_type", name="person_job_person_id_job_code_job_type_key"
        ),
        Index("idx_person_job_code_person", "job_code", "person_id"),
        Index("idx_person_job_person_type", "person_id", "job_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    job_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonSkill(Base):
    __tablename__ = "person_skill"
    __table_args__ = (
        CheckConstraint(
            "experience_months IS NULL OR experience_months >= 0",
            name="person_skill_experience_months_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="person_skill_source_type_check",
        ),
        UniqueConstraint("person_id", "tech_code", name="person_skill_person_id_tech_code_key"),
        Index("idx_person_skill_code_person", "tech_code", "person_id"),
        # idx_person_skill_person_recent uses last_used_year DESC — Alembic/migration-only
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    tech_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=False
    )
    last_used_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    experience_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonExpertise(Base):
    __tablename__ = "person_expertise"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('EXPLICIT', 'INFERRED')",
            name="person_expertise_evidence_type_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="person_expertise_source_type_check",
        ),
        UniqueConstraint("person_id", "exp_code", name="person_expertise_person_id_exp_code_key"),
        Index("idx_person_expertise_code_person", "exp_code", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    exp_code: Mapped[str] = mapped_column(
        String(100), ForeignKey("code_master.code", ondelete="RESTRICT"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="EXPLICIT")
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmploymentHistory(Base):
    __tablename__ = "employment_history"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="employment_history_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="employment_history_source_type_check",
        ),
        # idx_employment_person_period (person_id, start_date DESC) — Alembic/migration-only
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Education(Base):
    __tablename__ = "education"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="education_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="education_source_type_check",
        ),
        Index("idx_education_person", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    school_name: Mapped[str] = mapped_column(String(300), nullable=False)
    major: Mapped[str | None] = mapped_column(String(300), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Certification(Base):
    __tablename__ = "certification"
    __table_args__ = (
        CheckConstraint(
            "expiry_date IS NULL OR acquired_date IS NULL OR expiry_date >= acquired_date",
            name="certification_check",
        ),
        CheckConstraint(
            "source_type IN ('USER', 'AI_CONFIRMED', 'MIGRATION')",
            name="certification_source_type_check",
        ),
        Index("idx_certification_person", "person_id"),
        # idx_certification_name_trgm GIN — Alembic/migration-only
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    certification_name: Mapped[str] = mapped_column(String(300), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    acquired_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AI_CONFIRMED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
