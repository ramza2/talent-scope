"""Analysis API schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

AnalysisStatus = Literal[
    "QUEUED", "PROCESSING", "REVIEWING", "CONFIRMED", "FAILED", "CANCELLED"
]
AnalysisType = Literal["PROFILE"]
ChangeType = Literal["SAME", "NEW", "UPDATE", "CONFLICT", "REVIEW"]
ReviewStatus = Literal["PENDING", "ACCEPTED", "REJECTED", "MODIFIED", "MERGED"]
EntityType = Literal[
    "PROFILE",
    "JOB",
    "TECH",
    "EXP",
    "EMPLOYMENT",
    "EDUCATION",
    "CERTIFICATION",
    "PROJECT",
]


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class CreateAnalysisRequest(BaseModel):
    person_id: UUID
    document_ids: list[UUID] = Field(min_length=1)
    analysis_type: AnalysisType = "PROFILE"

    @field_validator("document_ids")
    @classmethod
    def unique_document_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("document_ids에 중복이 있습니다.")
        return value


class CreateAnalysisResponseData(BaseModel):
    analysis_id: UUID
    status: AnalysisStatus


class CreateAnalysisResponse(BaseModel):
    data: CreateAnalysisResponseData


class DiffCounts(BaseModel):
    same: int = 0
    new: int = 0
    update: int = 0
    conflict: int = 0
    review: int = 0
    pending: int = 0


class PersonBrief(BaseModel):
    id: UUID
    name: str


class DocumentBrief(BaseModel):
    id: UUID
    original_filename: str
    version_no: int | None = None
    document_type_code: str | None = None
    document_type_name: str | None = None


class AnalysisListItem(BaseModel):
    analysis_id: UUID
    person: PersonBrief
    documents: list[str] = Field(default_factory=list)
    status: AnalysisStatus
    counts: DiffCounts = Field(default_factory=DiffCounts)
    base_profile_version: int | None = None
    llm_model: str | None = None
    prompt_version: str | None = None
    overall_confidence: Decimal | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class AnalysisListResponse(BaseModel):
    data: list[AnalysisListItem]
    meta: PageMeta


class AnalysisDetail(BaseModel):
    analysis_id: UUID
    status: AnalysisStatus
    analysis_type: AnalysisType = "PROFILE"
    person: PersonBrief
    documents: list[DocumentBrief] = Field(default_factory=list)
    counts: DiffCounts = Field(default_factory=DiffCounts)
    candidate_json: dict[str, Any] = Field(default_factory=dict)
    base_profile_version: int | None = None
    llm_model: str | None = None
    vlm_model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    overall_confidence: Decimal | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AnalysisDetailResponse(BaseModel):
    data: AnalysisDetail


class EvidenceLite(BaseModel):
    id: UUID | None = None
    document_id: UUID | str | None = None
    page_no: int | None = None
    quote_text: str | None = None


class DiffItemResponse(BaseModel):
    id: UUID
    entity_type: str
    candidate_path: str | None = None
    existing_target_id: UUID | None = None
    field_name: str | None = None
    change_type: ChangeType
    old_value: Any | None = None
    new_value: Any | None = None
    confidence: Decimal | None = None
    evidence_type: str | None = None
    review_status: ReviewStatus
    decided_value: Any | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    evidence: list[EvidenceLite] = Field(default_factory=list)


class DiffListResponse(BaseModel):
    data: list[DiffItemResponse]


class DiffDecisionRequest(BaseModel):
    review_status: ReviewStatus
    decided_value: Any | None = None
    existing_target_id: UUID | None = None


class DiffDecisionResponse(BaseModel):
    data: DiffItemResponse


class BulkDiffRequest(BaseModel):
    diff_ids: list[UUID] = Field(min_length=1)
    review_status: ReviewStatus


class BulkDiffResponse(BaseModel):
    data: list[DiffItemResponse]


class RetryAnalysisResponse(BaseModel):
    data: CreateAnalysisResponseData


class ConfirmAnalysisRequest(BaseModel):
    expected_profile_version: int = Field(ge=1)


class ConfirmAnalysisResponseData(BaseModel):
    analysis_id: UUID
    person_id: UUID
    profile_version: int
    status: AnalysisStatus
    search_index_status: str = "PENDING"


class ConfirmAnalysisResponse(BaseModel):
    data: ConfirmAnalysisResponseData
