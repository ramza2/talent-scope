"""Users HTTP endpoints (ADMIN only)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    get_session_store,
    require_admin,
    require_csrf,
)
from app.modules.auth.session_store import SessionStore
from app.modules.users.schemas import (
    ResetPasswordRequest,
    UserCreateRequest,
    UserDetailResponse,
    UserListResponse,
    UserUpdateRequest,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(
    db: Session = Depends(get_db),
    sessions: SessionStore = Depends(get_session_store),
) -> UserService:
    return UserService(db=db, sessions=sessions)


@router.get("", response_model=UserListResponse)
def list_users(
    q: str | None = Query(default=None),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: AuthenticatedContext = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserListResponse:
    items, meta = service.list_users(
        q=q,
        role=role,
        status=status,
        page=page,
        page_size=page_size,
    )
    return UserListResponse(data=items, meta=meta)


@router.post("", response_model=UserDetailResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: UserService = Depends(get_user_service),
) -> UserDetailResponse:
    item = service.create_user(payload, actor_user_id=ctx.user.id)
    return UserDetailResponse(data=item)


@router.get("/{user_id}", response_model=UserDetailResponse)
def get_user(
    user_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserDetailResponse:
    return UserDetailResponse(data=service.get_user(user_id))


@router.patch("/{user_id}", response_model=UserDetailResponse)
def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: UserService = Depends(get_user_service),
) -> UserDetailResponse:
    item = service.update_user(user_id, payload, actor_user_id=ctx.user.id)
    return UserDetailResponse(data=item)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: UUID,
    payload: ResetPasswordRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: UserService = Depends(get_user_service),
) -> Response:
    service.reset_password(user_id, payload, actor_user_id=ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
