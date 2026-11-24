"""V41 admin-only orphan diagnostics and explicitly requested recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.analysis.execution_guard import worker_guard_held
from app.modules.analysis.orphan_recovery import (
    evaluate_analysis,
    inspect_worker,
    list_stale_candidates,
    recover_orphan,
)
from app.modules.auth.dependencies import AuthenticatedContext, require_admin, require_csrf

router = APIRouter(prefix="/analyses", tags=["analyses"])


class OrphanCheckData(BaseModel):
    analysis_id: UUID
    status: str
    started_at: datetime | None
    updated_at: datetime | None
    stale: bool
    worker_task_state: Literal["ACTIVE", "ABSENT", "UNKNOWN", "NOT_CHECKED"]
    recovery_allowed: bool
    reason: str


class OrphanCheckResponse(BaseModel):
    data: OrphanCheckData


class OrphanCandidatesResponse(BaseModel):
    data: list[OrphanCheckData]


class OrphanRecoverData(BaseModel):
    analysis_id: UUID
    status: Literal["FAILED"]
    recovered: bool


class OrphanRecoverResponse(BaseModel):
    data: OrphanRecoverData


@router.get("/orphan-candidates", response_model=OrphanCandidatesResponse)
def get_orphan_candidates(
    _admin: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OrphanCandidatesResponse:
    candidates = list_stale_candidates(db)
    snapshot = inspect_worker() if candidates else None
    items = []
    for run in candidates:
        state = snapshot.target_state(run.id) if snapshot is not None else "NOT_CHECKED"
        reason = snapshot.reason if snapshot is not None else "NO_CANDIDATES"
        if worker_guard_held(db, run.id):
            state, reason = "ACTIVE", "DB_EXECUTION_GUARD_HELD"
        items.append(
            OrphanCheckData(
                analysis_id=run.id,
                status=run.status,
                started_at=run.started_at,
                updated_at=run.updated_at,
                stale=True,
                worker_task_state=state,
                recovery_allowed=state == "ABSENT",
                reason=reason,
            )
        )
    return OrphanCandidatesResponse(data=items)


@router.get("/{analysis_id}/orphan-check", response_model=OrphanCheckResponse)
def get_orphan_check(
    analysis_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OrphanCheckResponse:
    return OrphanCheckResponse(data=OrphanCheckData(**evaluate_analysis(db, analysis_id)))


@router.post("/{analysis_id}/recover-orphan", response_model=OrphanRecoverResponse)
def post_recover_orphan(
    analysis_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> OrphanRecoverResponse:
    result = recover_orphan(db, analysis_id, actor_user_id=ctx.user.id)
    return OrphanRecoverResponse(data=OrphanRecoverData(**result))
