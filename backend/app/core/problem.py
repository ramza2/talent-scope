"""Problem+JSON exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import TalentScopeError


def problem_type_url(code: str) -> str:
    slug = code.lower().replace("_", "-")
    return f"https://talentscope/errors/{slug}"


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": problem_type_url(code),
            "title": title,
            "status": status,
            "code": code,
            "detail": detail,
            "instance": instance,
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(TalentScopeError)
    async def handle_talentscope_error(
        request: Request, exc: TalentScopeError
    ) -> JSONResponse:
        return problem_response(
            status=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            instance=str(request.url.path),
        )
