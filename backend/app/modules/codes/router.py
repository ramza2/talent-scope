"""Codes HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_authenticated_user,
    require_csrf,
)
from app.modules.codes.schemas import (
    AliasReplaceRequest,
    CodeCreateRequest,
    CodeDetailResponse,
    CodeListResponse,
    CodeUpdateRequest,
)
from app.modules.codes.service import CodeService

router = APIRouter(prefix="/codes", tags=["codes"])


def get_code_service(db: Session = Depends(get_db)) -> CodeService:
    return CodeService(db)


@router.get("", response_model=CodeListResponse)
def list_codes(
    type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    parent_code: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=200),
    _auth: AuthenticatedContext = Depends(require_authenticated_user),
    service: CodeService = Depends(get_code_service),
) -> CodeListResponse:
    items = service.list_codes(
        code_type=type,
        q=q,
        parent_code=parent_code,
        active=active,
        page=page,
        page_size=page_size,
    )
    return CodeListResponse(data=items)


@router.get("/{code}", response_model=CodeDetailResponse)
def get_code(
    code: str,
    _auth: AuthenticatedContext = Depends(require_authenticated_user),
    service: CodeService = Depends(get_code_service),
) -> CodeDetailResponse:
    return CodeDetailResponse(data=service.get_code(code))


@router.post("", response_model=CodeDetailResponse, status_code=status.HTTP_201_CREATED)
def create_code(
    payload: CodeCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CodeService = Depends(get_code_service),
) -> CodeDetailResponse:
    item = service.create_code(payload, actor_user_id=ctx.user.id)
    return CodeDetailResponse(data=item)


@router.patch("/{code}", response_model=CodeDetailResponse)
def update_code(
    code: str,
    payload: CodeUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CodeService = Depends(get_code_service),
) -> CodeDetailResponse:
    item = service.update_code(code, payload, actor_user_id=ctx.user.id)
    return CodeDetailResponse(data=item)


@router.put("/{code}/aliases", response_model=CodeDetailResponse)
def replace_aliases(
    code: str,
    payload: AliasReplaceRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CodeService = Depends(get_code_service),
) -> CodeDetailResponse:
    item = service.replace_aliases(code, payload, actor_user_id=ctx.user.id)
    return CodeDetailResponse(data=item)
