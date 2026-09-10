"""Evidence API / materialization schemas and target vocabulary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EVIDENCE_TARGET_TYPES: frozenset[str] = frozenset(
    {
        "PERSON_PROFILE",
        "PERSON_JOB",
        "PERSON_SKILL",
        "PERSON_EXPERTISE",
        "EMPLOYMENT_HISTORY",
        "EDUCATION",
        "CERTIFICATION",
        "PROJECT",
    }
)

EvidenceTargetType = Literal[
    "PERSON_PROFILE",
    "PERSON_JOB",
    "PERSON_SKILL",
    "PERSON_EXPERTISE",
    "EMPLOYMENT_HISTORY",
    "EDUCATION",
    "CERTIFICATION",
    "PROJECT",
]


class EvidenceDocumentBrief(BaseModel):
    id: UUID
    title: str | None = None
    original_filename: str | None = None
    version_no: int | None = None


class EvidenceListItem(BaseModel):
    id: UUID
    document: EvidenceDocumentBrief
    page_no: int | None = None
    quote_text: str | None = None
    bbox: dict[str, Any] | None = None
    char_start: int | None = None
    char_end: int | None = None
    extraction_method: str | None = None
    relation_type: str = "SUPPORTS"


class EvidenceListResponse(BaseModel):
    data: list[EvidenceListItem]


class EvidenceLinkItem(BaseModel):
    target_type: str
    target_id: UUID
    field_name: str | None = None
    relation_type: str = "SUPPORTS"


class EvidenceDetail(BaseModel):
    id: UUID
    document: EvidenceDocumentBrief
    page_no: int | None = None
    quote_text: str | None = None
    bbox: dict[str, Any] | None = None
    char_start: int | None = None
    char_end: int | None = None
    extraction_method: str | None = None
    created_at: datetime | None = None
    links: list[EvidenceLinkItem] = Field(default_factory=list)


class EvidenceDetailResponse(BaseModel):
    data: EvidenceDetail
