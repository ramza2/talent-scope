"""Auth API schemas."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)


class AuthUserData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login_id: str
    name: str
    role: str
    email: str | None = None
    department: str | None = None


class AuthUserResponse(BaseModel):
    data: AuthUserData
