"""Dashboard orchestration."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.schemas import (
    AnalysisSummary,
    DashboardData,
    DashboardPermissions,
    DocumentFailureItem,
    PeopleSummary,
    RecentAnalysisItem,
    RecentPersonItem,
)

PROCESSING_ERROR_MAX_CHARS = 500
RECENT_LIMIT = 5


class DashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DashboardRepository(db)

    def get_dashboard(self, *, is_admin: bool) -> DashboardData:
        people = PeopleSummary(**self.repo.people_summary())
        recent_people = [
            RecentPersonItem(**row) for row in self.repo.recent_people(limit=RECENT_LIMIT)
        ]
        permissions = DashboardPermissions(
            can_manage_people=is_admin,
            can_manage_analyses=is_admin,
        )

        if not is_admin:
            return DashboardData(
                people=people,
                recent_people=recent_people,
                permissions=permissions,
                analysis=None,
                recent_analyses=None,
                document_failures=None,
            )

        status_counts = self.repo.analysis_status_summary()
        reviewing = int(status_counts.get("REVIEWING", 0))
        analysis = AnalysisSummary(
            queued=int(status_counts.get("QUEUED", 0)),
            processing=int(status_counts.get("PROCESSING", 0)),
            reviewing=reviewing,
            failed=int(status_counts.get("FAILED", 0)),
            review_pending_runs=reviewing,
        )
        recent_analyses = [
            RecentAnalysisItem(**row)
            for row in self.repo.recent_analyses(limit=RECENT_LIMIT)
        ]
        document_failures = [
            DocumentFailureItem(
                document_id=row["document_id"],
                person_id=row["person_id"],
                person_name=row["person_name"],
                original_filename=row["original_filename"],
                processing_status=row["processing_status"],
                processing_error=_truncate_error(row.get("processing_error")),
                uploaded_at=row["uploaded_at"],
            )
            for row in self.repo.recent_failed_documents(limit=RECENT_LIMIT)
        ]
        return DashboardData(
            people=people,
            recent_people=recent_people,
            permissions=permissions,
            analysis=analysis,
            recent_analyses=recent_analyses,
            document_failures=document_failures,
        )


def _truncate_error(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= PROCESSING_ERROR_MAX_CHARS:
        return text
    return text[: PROCESSING_ERROR_MAX_CHARS - 1] + "…"
