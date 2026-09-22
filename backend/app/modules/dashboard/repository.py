"""Read-only dashboard aggregates."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.document import Document, DocumentGroup
from app.db.models.person import Person, PersonProfile

_VISIBLE_STATUSES = ("ACTIVE", "INACTIVE", "ARCHIVED")
_OPS_ANALYSIS_STATUSES = ("QUEUED", "PROCESSING", "REVIEWING", "FAILED")
_ACTIONABLE_CHANGE_TYPES = ("NEW", "UPDATE", "CONFLICT", "REVIEW")


class DashboardRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def people_summary(self) -> dict[str, int]:
        row = self.db.execute(
            select(
                func.count().label("total"),
                func.coalesce(
                    func.sum(case((Person.status == "ACTIVE", 1), else_=0)), 0
                ).label("active"),
                func.coalesce(
                    func.sum(case((Person.status == "INACTIVE", 1), else_=0)), 0
                ).label("inactive"),
                func.coalesce(
                    func.sum(case((Person.status == "ARCHIVED", 1), else_=0)), 0
                ).label("archived"),
            ).where(
                Person.deleted_at.is_(None),
                Person.status.in_(_VISIBLE_STATUSES),
            )
        ).one()
        return {
            "total": int(row.total or 0),
            "active": int(row.active or 0),
            "inactive": int(row.inactive or 0),
            "archived": int(row.archived or 0),
        }

    def recent_people(self, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(
                Person.id,
                Person.status,
                Person.created_at,
                PersonProfile.name,
                PersonProfile.affiliation_company,
                PersonProfile.technical_grade,
                PersonProfile.profile_updated_at,
            )
            .outerjoin(PersonProfile, PersonProfile.person_id == Person.id)
            .where(
                Person.deleted_at.is_(None),
                Person.status.in_(_VISIBLE_STATUSES),
            )
            .order_by(Person.created_at.desc(), Person.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "person_id": row.id,
                "name": row.name,
                "status": row.status,
                "affiliation_company": row.affiliation_company,
                "technical_grade": row.technical_grade,
                "created_at": row.created_at,
                "profile_updated_at": row.profile_updated_at,
            }
            for row in rows
        ]

    def analysis_status_summary(self) -> dict[str, int]:
        rows = self.db.execute(
            select(AnalysisRun.status, func.count())
            .where(AnalysisRun.status.in_(_OPS_ANALYSIS_STATUSES))
            .group_by(AnalysisRun.status)
        ).all()
        counts = {status: 0 for status in _OPS_ANALYSIS_STATUSES}
        for status, count in rows:
            counts[str(status)] = int(count)
        return counts

    def recent_analyses(self, *, limit: int = 5) -> list[dict[str, Any]]:
        pending_subq = (
            select(
                AnalysisDiffItem.analysis_run_id.label("run_id"),
                func.count().label("pending_count"),
            )
            .where(
                AnalysisDiffItem.review_status == "PENDING",
                AnalysisDiffItem.change_type.in_(_ACTIONABLE_CHANGE_TYPES),
            )
            .group_by(AnalysisDiffItem.analysis_run_id)
            .subquery()
        )
        rows = self.db.execute(
            select(
                AnalysisRun.id,
                AnalysisRun.person_id,
                AnalysisRun.status,
                AnalysisRun.created_at,
                AnalysisRun.completed_at,
                PersonProfile.name,
                func.coalesce(pending_subq.c.pending_count, 0).label("pending_count"),
            )
            .outerjoin(PersonProfile, PersonProfile.person_id == AnalysisRun.person_id)
            .outerjoin(pending_subq, pending_subq.c.run_id == AnalysisRun.id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "analysis_id": row.id,
                "person_id": row.person_id,
                "person_name": row.name,
                "status": row.status,
                "pending_count": int(row.pending_count or 0),
                "created_at": row.created_at,
                "completed_at": row.completed_at,
            }
            for row in rows
        ]

    def recent_failed_documents(self, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(
                Document.id,
                Document.original_filename,
                Document.processing_status,
                Document.processing_error,
                Document.uploaded_at,
                Document.updated_at,
                DocumentGroup.person_id,
                PersonProfile.name,
            )
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .join(Person, Person.id == DocumentGroup.person_id)
            .outerjoin(PersonProfile, PersonProfile.person_id == Person.id)
            .where(
                Document.deleted_at.is_(None),
                DocumentGroup.deleted_at.is_(None),
                Person.deleted_at.is_(None),
                Person.status != "DELETED",
                Document.processing_status == "FAILED",
            )
            .order_by(Document.updated_at.desc(), Document.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "document_id": row.id,
                "person_id": row.person_id,
                "person_name": row.name,
                "original_filename": row.original_filename,
                "processing_status": row.processing_status,
                "processing_error": row.processing_error,
                "uploaded_at": row.uploaded_at,
            }
            for row in rows
        ]
