"""Search index DTOs and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

SEARCH_DOCUMENT_VERSION = "search-doc-v1"

OBJECT_TYPE_PROFILE = "PROFILE"
OBJECT_TYPE_PROJECT = "PROJECT"

SOURCE_WEIGHT_DEFAULT = Decimal("1.000")

PROFILE_SEARCH_TEXT_MAX_CHARS = 40_000
PROJECT_SEARCH_TEXT_MAX_CHARS = 15_000
SECTION_VALUE_MAX_CHARS = 4_000

GRADE_LABELS: dict[str, str] = {
    "BEGINNER": "초급",
    "INTERMEDIATE": "중급",
    "ADVANCED": "고급",
    "EXPERT": "특급",
    "UNKNOWN": "미상",
}

ERROR_MESSAGE_MAX_CHARS = 500  # safe truncated job.error_message length

ObjectType = Literal["PROFILE", "PROJECT"]


@dataclass(frozen=True)
class SearchDocument:
    person_id: UUID
    object_type: ObjectType
    object_id: UUID
    search_text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_weight: Decimal = SOURCE_WEIGHT_DEFAULT
