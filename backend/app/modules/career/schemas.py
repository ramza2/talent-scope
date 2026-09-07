"""Career API schemas — employment, education, certification."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class EmploymentItem(BaseModel):
    id: UUID
    person_id: UUID
    company_name: str
    department: str | None = None
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    responsibilities: str | None = None
    source_type: str
    created_at: datetime
    updated_at: datetime


class EmploymentListResponse(BaseModel):
    data: list[EmploymentItem]


class EmploymentDetailResponse(BaseModel):
    data: EmploymentItem


class EmploymentCreateRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=300)
    department: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    responsibilities: str | None = None

    @field_validator("company_name")
    @classmethod
    def strip_company(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("회사명은 필수입니다.")
        return cleaned

    @model_validator(mode="after")
    def validate_dates(self) -> EmploymentCreateRequest:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self


class EmploymentUpdateRequest(BaseModel):
    company_name: str | None = Field(default=None, max_length=300)
    department: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    responsibilities: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> EmploymentUpdateRequest:
        if "company_name" in self.model_fields_set:
            if self.company_name is None:
                raise ValueError("회사명은 null일 수 없습니다.")
            cleaned = self.company_name.strip()
            if not cleaned:
                raise ValueError("회사명은 비어 있을 수 없습니다.")
            self.company_name = cleaned
        if (
            "start_date" in self.model_fields_set
            and "end_date" in self.model_fields_set
            and self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self


class EducationItem(BaseModel):
    id: UUID
    person_id: UUID
    school_name: str
    major: str | None = None
    degree: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = None
    source_type: str
    created_at: datetime
    updated_at: datetime


class EducationListResponse(BaseModel):
    data: list[EducationItem]


class EducationDetailResponse(BaseModel):
    data: EducationItem


class EducationCreateRequest(BaseModel):
    school_name: str = Field(min_length=1, max_length=300)
    major: str | None = Field(default=None, max_length=300)
    degree: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, max_length=100)

    @field_validator("school_name")
    @classmethod
    def strip_school(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("학교명은 필수입니다.")
        return cleaned

    @model_validator(mode="after")
    def validate_dates(self) -> EducationCreateRequest:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self


class EducationUpdateRequest(BaseModel):
    school_name: str | None = Field(default=None, max_length=300)
    major: str | None = Field(default=None, max_length=300)
    degree: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    end_date: date | None = None
    status: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_fields(self) -> EducationUpdateRequest:
        if "school_name" in self.model_fields_set:
            if self.school_name is None:
                raise ValueError("학교명은 null일 수 없습니다.")
            cleaned = self.school_name.strip()
            if not cleaned:
                raise ValueError("학교명은 비어 있을 수 없습니다.")
            self.school_name = cleaned
        if (
            "start_date" in self.model_fields_set
            and "end_date" in self.model_fields_set
            and self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        return self


class CertificationItem(BaseModel):
    id: UUID
    person_id: UUID
    certification_name: str
    issuer: str | None = None
    acquired_date: date | None = None
    expiry_date: date | None = None
    certificate_no: str | None = None
    source_type: str
    created_at: datetime
    updated_at: datetime


class CertificationListResponse(BaseModel):
    data: list[CertificationItem]


class CertificationDetailResponse(BaseModel):
    data: CertificationItem


class CertificationCreateRequest(BaseModel):
    certification_name: str = Field(min_length=1, max_length=300)
    issuer: str | None = Field(default=None, max_length=300)
    acquired_date: date | None = None
    expiry_date: date | None = None
    certificate_no: str | None = Field(default=None, max_length=200)

    @field_validator("certification_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("자격명은 필수입니다.")
        return cleaned

    @model_validator(mode="after")
    def validate_dates(self) -> CertificationCreateRequest:
        if (
            self.acquired_date is not None
            and self.expiry_date is not None
            and self.expiry_date < self.acquired_date
        ):
            raise ValueError("만료일은 취득일보다 빠를 수 없습니다.")
        return self


class CertificationUpdateRequest(BaseModel):
    certification_name: str | None = Field(default=None, max_length=300)
    issuer: str | None = Field(default=None, max_length=300)
    acquired_date: date | None = None
    expiry_date: date | None = None
    certificate_no: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_fields(self) -> CertificationUpdateRequest:
        if "certification_name" in self.model_fields_set:
            if self.certification_name is None:
                raise ValueError("자격명은 null일 수 없습니다.")
            cleaned = self.certification_name.strip()
            if not cleaned:
                raise ValueError("자격명은 비어 있을 수 없습니다.")
            self.certification_name = cleaned
        if (
            "acquired_date" in self.model_fields_set
            and "expiry_date" in self.model_fields_set
            and self.acquired_date is not None
            and self.expiry_date is not None
            and self.expiry_date < self.acquired_date
        ):
            raise ValueError("만료일은 취득일보다 빠를 수 없습니다.")
        return self
