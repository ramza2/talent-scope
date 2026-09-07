"""Pydantic schemas for AI identity extraction."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class IdentityExtraction(BaseModel):
    """Minimal person identity extracted from upload documents.

    Extra fields from the model are ignored and never persisted.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=150)
    company: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)

    @field_validator("name", "company", "phone", "email", mode="before")
    @classmethod
    def _normalize_optional_str(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return str(value)
        return _blank_to_none(value)

    def has_any_signal(self) -> bool:
        return any([self.name, self.company, self.phone, self.email])
