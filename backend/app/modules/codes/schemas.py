"""Codes API request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

CodeType = Literal["JOB", "TECH", "EXP", "BIZ", "CUSTOMER_TYPE", "DOC_TYPE"]

ALLOWED_CODE_TYPES: frozenset[str] = frozenset(
    {"JOB", "TECH", "EXP", "BIZ", "CUSTOMER_TYPE", "DOC_TYPE"}
)


class CodeItem(BaseModel):
    code: str
    type: CodeType
    name: str
    description: str | None = None
    parent_code: str | None = None
    sort_order: int = 0
    aliases: list[str] = Field(default_factory=list)
    is_active: bool = True


class CodeListResponse(BaseModel):
    data: list[CodeItem]


class CodeDetailResponse(BaseModel):
    data: CodeItem


class CodeCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    type: CodeType
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    parent_code: str | None = None
    sort_order: int = 0
    aliases: list[str] = Field(default_factory=list)

    @field_validator("code", "name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("빈 값은 허용되지 않습니다.")
        return cleaned

    @field_validator("parent_code")
    @classmethod
    def strip_parent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class CodeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    parent_code: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("빈 값은 허용되지 않습니다.")
        return cleaned


class AliasReplaceRequest(BaseModel):
    aliases: list[str] = Field(default_factory=list)
