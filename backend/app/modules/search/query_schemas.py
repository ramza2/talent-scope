"""Pydantic schemas for POST /search/people (read-only hybrid search)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import get_settings

SkillMatchMode = Literal["ANY", "ALL"]
SearchSort = Literal[
    "RELEVANCE",
    "CAREER_DESC",
    "UPDATED_DESC",
    "RECENT_PROJECT_DESC",
    "NAME_ASC",
]
MatchType = Literal["REQUIRED", "PREFERRED"]
MatchStatus = Literal["MATCH", "NO_MATCH"]

TECHNICAL_GRADES = frozenset(
    {"BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT", "UNKNOWN"}
)


def _normalize_code_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        code = (raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _normalize_text_list(values: list[str] | None) -> list[str]:
    if not values:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = (raw or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out


class GradeFilter(BaseModel):
    values: list[str] = Field(default_factory=list)

    @field_validator("values", mode="before")
    @classmethod
    def _clean_values(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("grade.values must be a list")
        cleaned = _normalize_code_list([str(v) for v in value])
        for grade in cleaned:
            if grade not in TECHNICAL_GRADES:
                raise ValueError(f"unsupported technical grade: {grade}")
        return cleaned


class CareerFilter(BaseModel):
    min_months: int | None = None
    max_months: int | None = None

    @field_validator("min_months", "max_months")
    @classmethod
    def _non_negative(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("career months must be >= 0")
        return value

    @model_validator(mode="after")
    def _range(self) -> CareerFilter:
        if (
            self.min_months is not None
            and self.max_months is not None
            and self.min_months > self.max_months
        ):
            raise ValueError("career.min_months must be <= career.max_months")
        return self


class SearchConditionBlock(BaseModel):
    jobs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    expertise: list[str] = Field(default_factory=list)
    business_domains: list[str] = Field(default_factory=list)
    customer_types: list[str] = Field(default_factory=list)
    grade: GradeFilter | None = None
    career: CareerFilter | None = None
    affiliations: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    project_keywords: list[str] = Field(default_factory=list)

    @field_validator(
        "jobs",
        "skills",
        "expertise",
        "business_domains",
        "customer_types",
        mode="before",
    )
    @classmethod
    def _codes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("code list must be a list")
        return _normalize_code_list([str(v) for v in value])

    @field_validator("affiliations", "certifications", "project_keywords", mode="before")
    @classmethod
    def _texts(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("text list must be a list")
        return _normalize_text_list([str(v) for v in value])


class PreferredConditionBlock(BaseModel):
    jobs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    expertise: list[str] = Field(default_factory=list)
    business_domains: list[str] = Field(default_factory=list)
    customer_types: list[str] = Field(default_factory=list)

    @field_validator(
        "jobs",
        "skills",
        "expertise",
        "business_domains",
        "customer_types",
        mode="before",
    )
    @classmethod
    def _codes(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("code list must be a list")
        return _normalize_code_list([str(v) for v in value])


class SearchPeopleRequest(BaseModel):
    required: SearchConditionBlock = Field(default_factory=SearchConditionBlock)
    preferred: PreferredConditionBlock = Field(default_factory=PreferredConditionBlock)
    skill_match_mode: SkillMatchMode = "ANY"
    semantic_query: str | None = None
    keyword_query: str | None = None
    sort: SearchSort = "RELEVANCE"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    suggest_relaxations: bool = False

    @field_validator("keyword_query", mode="before")
    @classmethod
    def _keyword(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > 500:
            raise ValueError("keyword_query must be at most 500 characters")
        return text

    @field_validator("semantic_query", mode="before")
    @classmethod
    def _semantic(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        cap = int(get_settings().embedding_max_input_chars)
        if len(text) > max(cap * 2, cap):
            raise ValueError("semantic_query is too long")
        return text


class MatchItem(BaseModel):
    condition: str
    type: MatchType
    status: MatchStatus
    evidence_count: int = 0


class SearchPersonSummary(BaseModel):
    name: str
    technical_grade: str | None = None
    career_months: int | None = None
    primary_jobs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    expertise: list[str] = Field(default_factory=list)


class TopProjectItem(BaseModel):
    project_id: UUID
    project_name: str
    period: str | None = None
    roles: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    evidence_id: UUID | None = None
    source_level: str
    target_type: str | None = None
    target_id: UUID | None = None
    field_name: str | None = None
    relation_type: str | None = None
    document_id: UUID | None = None
    document_title: str | None = None
    original_filename: str | None = None
    version_no: int | None = None
    page_no: int | None = None
    snippet: str | None = None


class SearchPersonResult(BaseModel):
    person_id: UUID
    score: int
    person: SearchPersonSummary
    matches: list[MatchItem] = Field(default_factory=list)
    top_projects: list[TopProjectItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class SearchMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int = 0
    candidate_limit_reached: bool = False


class SearchPeopleResponse(BaseModel):
    data: list[SearchPersonResult]
    meta: SearchMeta
    query: dict[str, Any]
    relaxations: list[dict[str, Any]] = Field(default_factory=list)
