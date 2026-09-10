"""Pydantic schemas for profile-candidate-v1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "profile-candidate-v1"
ALLOWED_GRADES = frozenset(
    {"BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT", "UNKNOWN"}
)
JOB_TYPES = frozenset({"PRIMARY", "SECONDARY", "EXPERIENCE"})
EVIDENCE_TYPES = frozenset({"EXPLICIT", "INFERRED"})
DATE_PRECISIONS = frozenset({"YEAR", "MONTH", "DAY"})

# Profile scalar fields that may carry per-field source_refs provenance.
PROFILE_SCALAR_FIELDS: tuple[str, ...] = (
    "name",
    "birth_year",
    "phone",
    "email",
    "address_region",
    "affiliation_company",
    "department",
    "current_title",
    "employment_type",
    "technical_grade",
    "career_start_date",
    "career_document_value",
    "profile_summary",
)
PROFILE_SCALAR_FIELD_NAMES = frozenset(PROFILE_SCALAR_FIELDS)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: str | None = None
    page_no: int | None = None
    quote_text: str | None = Field(default=None, max_length=500)


class DatedValue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_value: str | None = None
    normalized_value: str | None = None
    precision: Literal["YEAR", "MONTH", "DAY"] | None = None


class ProfileCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=150)
    birth_year: int | None = None
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address_region: str | None = Field(default=None, max_length=200)
    affiliation_company: str | None = Field(default=None, max_length=300)
    department: str | None = Field(default=None, max_length=200)
    current_title: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=50)
    technical_grade: str | None = None
    career_start_date: str | None = None
    career_document_value: str | None = None
    profile_summary: str | None = None
    # Optional per-field provenance map. Keys limited to PROFILE_SCALAR_FIELDS.
    source_refs: dict[str, list[SourceRef]] = Field(default_factory=dict)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _profile_source_refs(cls, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, Any] = {}
        for key, refs in value.items():
            field = str(key).strip()
            if field not in PROFILE_SCALAR_FIELD_NAMES:
                continue
            if isinstance(refs, list):
                out[field] = refs
        return out

    @field_validator("technical_grade", mode="before")
    @classmethod
    def _grade(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip().upper()
        mapping = {
            "초급": "BEGINNER",
            "중급": "INTERMEDIATE",
            "고급": "ADVANCED",
            "특급": "EXPERT",
            "BEGINNER": "BEGINNER",
            "INTERMEDIATE": "INTERMEDIATE",
            "ADVANCED": "ADVANCED",
            "EXPERT": "EXPERT",
            "UNKNOWN": "UNKNOWN",
        }
        mapped = mapping.get(text) or mapping.get(str(value).strip())
        if mapped in ALLOWED_GRADES:
            return mapped
        return "UNKNOWN"


class JobCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_value: str | None = None
    code: str | None = None
    job_type: str | None = "PRIMARY"
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)

    @field_validator("job_type", mode="before")
    @classmethod
    def _job_type(cls, value: object) -> str:
        text = str(value or "PRIMARY").strip().upper()
        return text if text in JOB_TYPES else "PRIMARY"


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_value: str | None = None
    code: str | None = None
    last_used_year: int | None = None
    experience_months: int | None = None
    is_representative: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class ExpertiseCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_value: str | None = None
    code: str | None = None
    evidence_type: str | None = "EXPLICIT"
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)

    @field_validator("evidence_type", mode="before")
    @classmethod
    def _ev(cls, value: object) -> str:
        text = str(value or "EXPLICIT").strip().upper()
        return text if text in EVIDENCE_TYPES else "EXPLICIT"


class EmploymentCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company_name: str | None = None
    department: str | None = None
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    responsibilities: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class EducationCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    school_name: str | None = None
    major: str | None = None
    degree: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class CertificationCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    certification_name: str | None = None
    issuer: str | None = None
    acquired_date: str | None = None
    expiry_date: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class CodeRefCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw_value: str | None = None
    code: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)


class ProjectCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_name: str | None = None
    customer_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    duration_months: int | None = None
    responsibilities: str | None = None
    project_summary: str | None = None
    jobs: list[CodeRefCandidate] = Field(default_factory=list)
    skills: list[CodeRefCandidate] = Field(default_factory=list)
    expertise: list[CodeRefCandidate] = Field(default_factory=list)
    business_domains: list[CodeRefCandidate] = Field(default_factory=list)
    customer_types: list[CodeRefCandidate] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_refs: list[SourceRef] = Field(default_factory=list)


class SummaryCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None


class AnalysisMetaCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_confidence: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class ProfileCandidateDocument(BaseModel):
    """Validated candidate_json stored on AnalysisRun."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = SCHEMA_VERSION
    profile: ProfileCandidate = Field(default_factory=ProfileCandidate)
    jobs: list[JobCandidate] = Field(default_factory=list)
    skills: list[SkillCandidate] = Field(default_factory=list)
    expertise: list[ExpertiseCandidate] = Field(default_factory=list)
    employment_history: list[EmploymentCandidate] = Field(default_factory=list)
    education: list[EducationCandidate] = Field(default_factory=list)
    certifications: list[CertificationCandidate] = Field(default_factory=list)
    projects: list[ProjectCandidate] = Field(default_factory=list)
    summary: SummaryCandidate = Field(default_factory=SummaryCandidate)
    analysis: AnalysisMetaCandidate = Field(default_factory=AnalysisMetaCandidate)

    def to_storage_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
