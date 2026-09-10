"""Evidence HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_authenticated_user,
)
from app.modules.evidence.schemas import EvidenceDetailResponse, EvidenceListResponse
from app.modules.evidence.service import EvidenceService

router = APIRouter(prefix="/evidence", tags=["evidence"])


def get_evidence_service(db: Session = Depends(get_db)) -> EvidenceService:
    return EvidenceService(db)


@router.get("", response_model=EvidenceListResponse)
def list_evidence(
    target_type: str = Query(...),
    target_id: UUID = Query(...),
    field_name: str | None = Query(default=None),
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: EvidenceService = Depends(get_evidence_service),
) -> EvidenceListResponse:
    items = service.list_for_target(
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
        is_admin=ctx.user.role == "ADMIN",
    )
    return EvidenceListResponse(data=items)


@router.get("/{evidence_id}", response_model=EvidenceDetailResponse)
def get_evidence(
    evidence_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: EvidenceService = Depends(get_evidence_service),
) -> EvidenceDetailResponse:
    return EvidenceDetailResponse(
        data=service.get_evidence(
            evidence_id, is_admin=ctx.user.role == "ADMIN"
        )
    )
