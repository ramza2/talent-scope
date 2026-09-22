"""Dashboard API schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PeopleSummary(BaseModel):
    total: int
    active: int
    inactive: int
    archived: int


class RecentPersonItem(BaseModel):
    person_id: UUID
    name: str | None = None
    status: str
    affiliation_company: str | None = None
    technical_grade: str | None = None
    created_at: datetime
    profile_updated_at: datetime | None = None


class DashboardPermissions(BaseModel):
    can_manage_people: bool
    can_manage_analyses: bool


class AnalysisSummary(BaseModel):
    queued: int = 0
    processing: int = 0
    reviewing: int = 0
    failed: int = 0
    review_pending_runs: int = 0


class RecentAnalysisItem(BaseModel):
    analysis_id: UUID
    person_id: UUID
    person_name: str | None = None
    status: str
    pending_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class DocumentFailureItem(BaseModel):
    document_id: UUID
    person_id: UUID
    person_name: str | None = None
    original_filename: str
    processing_status: str
    processing_error: str | None = None
    uploaded_at: datetime


class DashboardData(BaseModel):
    people: PeopleSummary
    recent_people: list[RecentPersonItem] = Field(default_factory=list)
    permissions: DashboardPermissions
    analysis: AnalysisSummary | None = None
    recent_analyses: list[RecentAnalysisItem] | None = None
    document_failures: list[DocumentFailureItem] | None = None


class DashboardResponse(BaseModel):
    data: DashboardData
