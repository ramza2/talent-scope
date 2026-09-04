"""People API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

PersonStatus = Literal["ACTIVE", "INACTIVE", "ARCHIVED", "DELETED"]
TechnicalGrade = Literal["BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT", "UNKNOWN"]
JobType = Literal["PRIMARY", "SECONDARY", "EXPERIENCE"]
EvidenceType = Literal["EXPLICIT", "INFERRED"]

ALLOWED_STATUSES: frozenset[str] = frozenset({"ACTIVE", "INACTIVE", "ARCHIVED", "DELETED"})
ALLOWED_GRADES: frozenset[str] = frozenset(
    {"BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT", "UNKNOWN"}
)
ALLOWED_JOB_TYPES: frozenset[str] = frozenset({"PRIMARY", "SECONDARY", "EXPERIENCE"})
ALLOWED_EVIDENCE: frozenset[str] = frozenset({"EXPLICIT", "INFERRED"})


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class CodeRef(BaseModel):
    code: str
    name: str


class JobItem(BaseModel):
    code: str
    name: str
    job_type: JobType
    sort_order: int = 0
    source_type: str | None = None


class SkillItem(BaseModel):
    code: str
    name: str
    last_used_year: int | None = None
    experience_months: int | None = None
    is_representative: bool = False
    source_type: str | None = None


class ExpertiseItem(BaseModel):
    code: str
    name: str
    evidence_type: EvidenceType = "EXPLICIT"
    source_type: str | None = None


class ProfileFields(BaseModel):
    name: str
    birth_year: int | None = None
    phone: str | None = None
    email: str | None = None
    address_region: str | None = None
    affiliation_company: str | None = None
    department: str | None = None
    current_title: str | None = None
    employment_type: str | None = None
    technical_grade: TechnicalGrade | None = None
    career_start_date: date | None = None
    career_calculated_months: int | None = None
    career_document_value: str | None = None
    career_confirmed_months: int | None = None
    profile_summary: str | None = None
    profile_updated_at: datetime | None = None


class PeopleListItem(BaseModel):
    id: UUID
    status: PersonStatus
    name: str
    primary_job: CodeRef | None = None
    technical_grade: TechnicalGrade | None = None
    career_confirmed_months: int | None = None
    affiliation_company: str | None = None
    skills: list[CodeRef] = Field(default_factory=list)
    expertise: list[CodeRef] = Field(default_factory=list)
    profile_version: int
    profile_updated_at: datetime | None = None
    updated_at: datetime


class PeopleListResponse(BaseModel):
    data: list[PeopleListItem]
    meta: PageMeta


class DocumentSummary(BaseModel):
    count: int = 0
    latest_document_at: datetime | None = None


class PendingAnalysis(BaseModel):
    id: UUID
    status: str


class RecentProject(BaseModel):
    id: UUID
    project_name: str
    customer_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class PersonDetail(BaseModel):
    id: UUID
    status: PersonStatus
    profile_version: int
    profile: ProfileFields
    jobs: list[JobItem] = Field(default_factory=list)
    skills: list[SkillItem] = Field(default_factory=list)
    expertise: list[ExpertiseItem] = Field(default_factory=list)
    business_domains: list[CodeRef] = Field(default_factory=list)
    customer_types: list[CodeRef] = Field(default_factory=list)
    recent_projects: list[RecentProject] = Field(default_factory=list)
    document_summary: DocumentSummary = Field(default_factory=DocumentSummary)
    pending_analysis: PendingAnalysis | None = None


class PersonDetailResponse(BaseModel):
    data: PersonDetail


class PersonCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address_region: str | None = Field(default=None, max_length=200)
    affiliation_company: str | None = Field(default=None, max_length=300)
    department: str | None = Field(default=None, max_length=200)
    current_title: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    technical_grade: TechnicalGrade | None = None
    career_start_date: date | None = None
    career_confirmed_months: int | None = Field(default=None, ge=0)
    profile_summary: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("이름은 필수입니다.")
        return cleaned


class PersonStatusUpdateRequest(BaseModel):
    status: PersonStatus


class ProfileUpdateRequest(BaseModel):
    """Partial profile update.

    Omitted fields keep existing values. Explicit ``name: null`` is rejected
    because ``person_profile.name`` is NOT NULL.
    """

    expected_profile_version: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=150)
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address_region: str | None = Field(default=None, max_length=200)
    affiliation_company: str | None = Field(default=None, max_length=300)
    department: str | None = Field(default=None, max_length=200)
    current_title: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    technical_grade: TechnicalGrade | None = None
    career_start_date: date | None = None
    career_confirmed_months: int | None = Field(default=None, ge=0)
    profile_summary: str | None = None

    @model_validator(mode="after")
    def validate_explicit_name(self) -> ProfileUpdateRequest:
        if "name" not in self.model_fields_set:
            return self
        if self.name is None:
            raise ValueError("이름은 null일 수 없습니다.")
        cleaned = self.name.strip()
        if not cleaned:
            raise ValueError("이름은 비어 있을 수 없습니다.")
        if len(cleaned) > 150:
            raise ValueError("이름은 150자를 초과할 수 없습니다.")
        self.name = cleaned
        return self


class JobWriteItem(BaseModel):
    job_code: str = Field(min_length=1, max_length=100)
    job_type: JobType
    sort_order: int = 0


class JobsReplaceRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    jobs: list[JobWriteItem] = Field(default_factory=list)


class SkillWriteItem(BaseModel):
    tech_code: str = Field(min_length=1, max_length=100)
    last_used_year: int | None = Field(default=None, ge=1900, le=2100)
    experience_months: int | None = Field(default=None, ge=0)
    is_representative: bool = False


class SkillsReplaceRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    skills: list[SkillWriteItem] = Field(default_factory=list)


class ExpertiseWriteItem(BaseModel):
    exp_code: str = Field(min_length=1, max_length=100)
    evidence_type: EvidenceType = "EXPLICIT"


class ExpertiseReplaceRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)
    expertise: list[ExpertiseWriteItem] = Field(default_factory=list)


class RevisionItem(BaseModel):
    revision_no: int
    source_type: str
    created_by: UUID | None = None
    created_by_name: str | None = None
    created_at: datetime
    snapshot: dict


class RevisionListResponse(BaseModel):
    data: list[RevisionItem]
    meta: PageMeta
