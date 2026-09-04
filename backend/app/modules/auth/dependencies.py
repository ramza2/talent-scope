"""Auth / CSRF / RBAC FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CsrfInvalidError,
    ForbiddenError,
    UnauthorizedError,
)
from app.core.security import constant_time_equals
from app.db.models.user import AppUser
from app.db.session import get_db
from app.modules.auth.service import AuthService
from app.modules.auth.session_store import SessionRecord, SessionStore


@dataclass(frozen=True)
class AuthenticatedContext:
    user: AppUser
    session_id: str
    session: SessionRecord


def get_session_store(settings: Settings = Depends(get_settings)) -> SessionStore:
    return SessionStore(settings=settings)


def get_auth_service(
    db: Session = Depends(get_db),
    sessions: SessionStore = Depends(get_session_store),
) -> AuthService:
    return AuthService(db=db, sessions=sessions)


def _read_session_cookie(
    request: Request,
    settings: Settings,
) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> AuthenticatedContext:
    session_id = _read_session_cookie(request, settings)
    if not session_id:
        raise UnauthorizedError("로그인이 필요합니다.")
    user, record = service.resolve_user_from_session(session_id)
    return AuthenticatedContext(user=user, session_id=session_id, session=record)


def require_authenticated_user(
    ctx: AuthenticatedContext = Depends(get_current_user),
) -> AuthenticatedContext:
    return ctx


def require_admin(
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
) -> AuthenticatedContext:
    if ctx.user.role != "ADMIN":
        raise ForbiddenError("관리자 권한이 필요합니다.")
    return ctx


def require_csrf(
    request: Request,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    settings: Settings = Depends(get_settings),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    csrf_cookie: str | None = Cookie(default=None, alias="ts_csrf"),
) -> AuthenticatedContext:
    cookie_name = settings.csrf_cookie_name
    cookie_token = request.cookies.get(cookie_name) or csrf_cookie
    header_token = x_csrf_token

    if not cookie_token or not header_token:
        raise CsrfInvalidError()

    if not constant_time_equals(cookie_token, header_token):
        raise CsrfInvalidError()

    if not constant_time_equals(header_token, ctx.session.csrf_token):
        raise CsrfInvalidError()

    return ctx
