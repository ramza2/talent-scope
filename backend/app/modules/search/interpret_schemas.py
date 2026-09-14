"""Pydantic schemas for POST /search/interpret."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.search.interpret_policy import (
    SEARCH_INTERPRET_MAX_TEXT_CHARS,
    SEARCH_QUERY_VERSION,
    assert_strict_interpret_payload,
    normalize_assumptions,
)
from app.modules.search.query_schemas import (
    PreferredConditionBlock,
    SearchConditionBlock,
    SearchSort,
    SkillMatchMode,
)


class SearchInterpretRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=SEARCH_INTERPRET_MAX_TEXT_CHARS)
    previous_query: SearchInterpretData | None = None

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        if value is None:
            raise ValueError("text is required")
        text = str(value).strip()
        if not text:
            raise ValueError("text must not be blank")
        if len(text) > SEARCH_INTERPRET_MAX_TEXT_CHARS:
            raise ValueError(
                f"text must be at most {SEARCH_INTERPRET_MAX_TEXT_CHARS} characters"
            )
        return text


class SearchInterpretLLMOutput(BaseModel):
    """Strict LLM JSON shape — root and nested extra fields forbidden."""

    model_config = ConfigDict(extra="forbid")

    required: SearchConditionBlock = Field(default_factory=SearchConditionBlock)
    preferred: PreferredConditionBlock = Field(default_factory=PreferredConditionBlock)
    skill_match_mode: SkillMatchMode = "ANY"
    semantic_query: str | None = None
    keyword_query: str | None = None
    sort: SearchSort = "RELEVANCE"
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_nested_unknown_fields(cls, value: Any) -> Any:
        # Shared SearchConditionBlock allows extra=ignore; enforce allowlist here.
        assert_strict_interpret_payload(value, include_query_version=False)
        return value

    @field_validator("assumptions", mode="before")
    @classmethod
    def _assumptions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("assumptions must be a list")
        return [str(v) for v in value]

    @field_validator("keyword_query", "semantic_query", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def _normalize_assumptions(self) -> SearchInterpretLLMOutput:
        self.assumptions = normalize_assumptions(self.assumptions)
        return self


class SearchInterpretData(BaseModel):
    """Client-facing interpret result (SearchPeopleRequest-compatible + meta)."""

    query_version: Literal["1.0"] = SEARCH_QUERY_VERSION  # type: ignore[assignment]
    required: SearchConditionBlock = Field(default_factory=SearchConditionBlock)
    preferred: PreferredConditionBlock = Field(default_factory=PreferredConditionBlock)
    skill_match_mode: SkillMatchMode = "ANY"
    semantic_query: str | None = None
    keyword_query: str | None = None
    sort: SearchSort = "RELEVANCE"
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _reject_nested_unknown_fields(cls, value: Any) -> Any:
        # previous_query JSON must reject nested extras. When constructing from
        # already-validated model instances (service success path), skip.
        if isinstance(value, dict):
            required = value.get("required")
            preferred = value.get("preferred")
            if (required is None or isinstance(required, dict)) and (
                preferred is None or isinstance(preferred, dict)
            ):
                assert_strict_interpret_payload(value, include_query_version=True)
        return value

    @field_validator("assumptions", mode="before")
    @classmethod
    def _assumptions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("assumptions must be a list")
        return normalize_assumptions([str(v) for v in value])

    @field_validator("keyword_query", "semantic_query", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SearchInterpretResponse(BaseModel):
    data: SearchInterpretData


# Rebuild forward ref for previous_query.
SearchInterpretRequest.model_rebuild()
