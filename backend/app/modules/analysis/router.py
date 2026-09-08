"""Analysis HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.analysis.schemas import (
    AnalysisDetailResponse,
    AnalysisListResponse,
    BulkDiffRequest,
    BulkDiffResponse,
    ConfirmAnalysisRequest,
    CreateAnalysisRequest,
    CreateAnalysisResponse,
    DiffDecisionRequest,
    DiffDecisionResponse,
    DiffListResponse,
    RetryAnalysisResponse,
)
from app.modules.analysis.service import AnalysisService
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_csrf,
)

router = APIRouter(prefix="/analyses", tags=["analyses"])


def get_analysis_service(db: Session = Depends(get_db)) -> AnalysisService:
    return AnalysisService(db)


@router.get("", response_model=AnalysisListResponse)
def list_analyses(
    status_filter: str | None = Query(default=None, alias="status"),
    person_id: UUID | None = Query(default=None),
    sort: str = Query(default="created_desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: AuthenticatedContext = Depends(require_admin),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisListResponse:
    items, meta = service.list_analyses(
        status=status_filter,
        person_id=person_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return AnalysisListResponse(data=items, meta=meta)


@router.post(
    "",
    response_model=CreateAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_analysis(
    payload: CreateAnalysisRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> CreateAnalysisResponse:
    data = service.create_analysis(payload, ctx.user.id)
    return CreateAnalysisResponse(data=data)


@router.get("/{analysis_id}", response_model=AnalysisDetailResponse)
def get_analysis(
    analysis_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    service: AnalysisService = Depends(get_analysis_service),
) -> AnalysisDetailResponse:
    return AnalysisDetailResponse(data=service.get_analysis(analysis_id))


@router.get("/{analysis_id}/diffs", response_model=DiffListResponse)
def list_diffs(
    analysis_id: UUID,
    change_types: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    _admin: AuthenticatedContext = Depends(require_admin),
    service: AnalysisService = Depends(get_analysis_service),
) -> DiffListResponse:
    return DiffListResponse(
        data=service.list_diffs(
            analysis_id,
            change_types=change_types,
            review_status=review_status,
            entity_type=entity_type,
        )
    )


@router.patch(
    "/{analysis_id}/diffs/{diff_id}",
    response_model=DiffDecisionResponse,
)
def review_diff(
    analysis_id: UUID,
    diff_id: UUID,
    payload: DiffDecisionRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> DiffDecisionResponse:
    return DiffDecisionResponse(
        data=service.review_diff(analysis_id, diff_id, payload, ctx.user.id)
    )


@router.post(
    "/{analysis_id}/diffs/bulk",
    response_model=BulkDiffResponse,
)
def bulk_review(
    analysis_id: UUID,
    payload: BulkDiffRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> BulkDiffResponse:
    return BulkDiffResponse(
        data=service.bulk_review(analysis_id, payload, ctx.user.id)
    )


@router.post("/{analysis_id}/confirm")
def confirm_analysis(
    analysis_id: UUID,
    payload: ConfirmAnalysisRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> None:
    _ = (payload, ctx)
    service.confirm_analysis(analysis_id)


@router.post(
    "/{analysis_id}/retry",
    response_model=RetryAnalysisResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_analysis(
    analysis_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AnalysisService = Depends(get_analysis_service),
) -> RetryAnalysisResponse:
    return RetryAnalysisResponse(data=service.retry_analysis(analysis_id, ctx.user.id))
