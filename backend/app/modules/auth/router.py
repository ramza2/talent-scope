"""Auth HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    get_auth_service,
    require_authenticated_user,
    require_csrf,
)
from app.modules.auth.schemas import AuthUserData, AuthUserResponse, LoginRequest
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(
    response: Response,
    *,
    settings: Settings,
    session_id: str,
    csrf_token: str,
) -> None:
    common = {
        "path": "/",
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "max_age": settings.session_ttl_seconds,
    }
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        httponly=True,
        **common,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        **common,
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )


@router.post("/login", response_model=AuthUserResponse, response_model_exclude_none=True)
def login(
    payload: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthUserResponse:
    result = service.login(payload.login_id, payload.password)
    _set_auth_cookies(
        response,
        settings=settings,
        session_id=result.session_id,
        csrf_token=result.csrf_token,
    )
    return AuthUserResponse(
        data=AuthUserData(
            id=result.user.id,
            login_id=result.user.login_id,
            name=result.user.name,
            role=result.user.role,
        )
    )


@router.get("/me", response_model=AuthUserResponse)
def me(
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
) -> AuthUserResponse:
    user = ctx.user
    return AuthUserResponse(
        data=AuthUserData(
            id=user.id,
            login_id=user.login_id,
            name=user.name,
            role=user.role,
            email=user.email,
            department=user.department,
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    service.logout(ctx.session_id, ctx.user)
    _clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
