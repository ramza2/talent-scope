"""Users API request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

UserRole = Literal["USER", "ADMIN"]
UserStatus = Literal["ACTIVE", "INACTIVE"]

ALLOWED_ROLES: frozenset[str] = frozenset({"USER", "ADMIN"})
ALLOWED_STATUSES: frozenset[str] = frozenset({"ACTIVE", "INACTIVE"})
MIN_PASSWORD_LENGTH = 8


class UserItem(BaseModel):
    id: UUID
    login_id: str
    name: str
    email: str | None = None
    department: str | None = None
    role: UserRole
    status: UserStatus
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class UserListResponse(BaseModel):
    data: list[UserItem]
    meta: PageMeta


class UserDetailResponse(BaseModel):
    data: UserItem


class UserCreateRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=200)
    role: UserRole = "USER"
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("login_id", "name")
    @classmethod
    def strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("빈 값은 허용되지 않습니다.")
        return cleaned

    @field_validator("email", "department")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")
        return value


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    department: str | None = Field(default=None, max_length=200)
    role: UserRole | None = None
    status: UserStatus | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("빈 값은 허용되지 않습니다.")
        return cleaned

    @field_validator("email", "department")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다.")
        return value
