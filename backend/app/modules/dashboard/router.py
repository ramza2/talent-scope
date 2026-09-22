"""Dashboard HTTP endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_authenticated_user,
)
from app.modules.dashboard.schemas import DashboardResponse
from app.modules.dashboard.service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def get_dashboard_service(db: Session = Depends(get_db)) -> DashboardService:
    return DashboardService(db)


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: DashboardService = Depends(get_dashboard_service),
) -> DashboardResponse:
    data = service.get_dashboard(is_admin=ctx.user.role == "ADMIN")
    return DashboardResponse(data=data)
