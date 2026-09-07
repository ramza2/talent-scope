"""Project API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.people.schemas import CodeRef, EvidenceType, PageMeta

ALLOWED_EVIDENCE: frozenset[str] = frozenset({"EXPLICIT", "INFERRED"})


class ProjectExpertiseWrite(BaseModel):
    exp_code: str = Field(min_length=1, max_length=100)
    evidence_type: EvidenceType = "EXPLICIT"


class ProjectExpertiseItem(BaseModel):
    code: str
    name: str
    evidence_type: EvidenceType = "EXPLICIT"


class ProjectDetail(BaseModel):
    id: UUID
    person_id: UUID
    project_name: str
    customer_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    duration_months: int | None = None
    responsibilities: str | None = None
    project_summary: str | None = None
    source_type: str
    source_analysis_run_id: UUID | None = None
    jobs: list[CodeRef] = Field(default_factory=list)
    skills: list[CodeRef] = Field(default_factory=list)
    expertise: list[ProjectExpertiseItem] = Field(default_factory=list)
    business_domains: list[CodeRef] = Field(default_factory=list)
    customer_types: list[CodeRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProjectDetailResponse(BaseModel):
    data: ProjectDetail


class ProjectListResponse(BaseModel):
    data: list[ProjectDetail]
    meta: PageMeta


class ProjectCreateRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=500)
    customer_name: str | None = Field(default=None, max_length=300)
    start_date: date | None = None
    end_date: date | None = None
    duration_months: int | None = Field(default=None, ge=0)
    responsibilities: str | None = None
    project_summary: str | None = None
    job_codes: list[str] = Field(default_factory=list)
    tech_codes: list[str] = Field(default_factory=list)
    expertise: list[ProjectExpertiseWrite] = Field(default_factory=list)
    biz_codes: list[str] = Field(default_factory=list)
    customer_type_codes: list[str] = Field(default_factory=list)

    @field_validator("project_name")
    @classmethod
    def strip_project_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("프로젝트명은 필수입니다.")
        return cleaned

    @field_validator("customer_name")
    @classmethod
    def strip_customer_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_dates(self) -> ProjectCreateRequest:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self


class ProjectUpdateRequest(BaseModel):
    """Partial project update. Omitted relation arrays keep existing links;
    empty arrays clear the relation set.
    """

    project_name: str | None = Field(default=None, max_length=500)
    customer_name: str | None = Field(default=None, max_length=300)
    start_date: date | None = None
    end_date: date | None = None
    duration_months: int | None = Field(default=None, ge=0)
    responsibilities: str | None = None
    project_summary: str | None = None
    job_codes: list[str] | None = None
    tech_codes: list[str] | None = None
    expertise: list[ProjectExpertiseWrite] | None = None
    biz_codes: list[str] | None = None
    customer_type_codes: list[str] | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> ProjectUpdateRequest:
        if "project_name" in self.model_fields_set:
            if self.project_name is None:
                raise ValueError("프로젝트명은 null일 수 없습니다.")
            cleaned = self.project_name.strip()
            if not cleaned:
                raise ValueError("프로젝트명은 비어 있을 수 없습니다.")
            if len(cleaned) > 500:
                raise ValueError("프로젝트명은 500자를 초과할 수 없습니다.")
            self.project_name = cleaned

        if "customer_name" in self.model_fields_set and self.customer_name is not None:
            cleaned = self.customer_name.strip()
            self.customer_name = cleaned or None

        start = self.start_date
        end = self.end_date
        # Cross-field date validation only when both present in this payload;
        # service also validates against persisted values after merge.
        if (
            "start_date" in self.model_fields_set
            and "end_date" in self.model_fields_set
            and start is not None
            and end is not None
            and end < start
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self
