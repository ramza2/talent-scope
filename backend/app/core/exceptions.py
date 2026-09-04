"""Application exception hierarchy with Problem+JSON codes."""

from __future__ import annotations


class TalentScopeError(Exception):
    """Base application error."""

    code: str = "INTERNAL_ERROR"
    title: str = "Internal error"
    status_code: int = 500

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.title
        super().__init__(self.detail)


class NotFoundError(TalentScopeError):
    code = "NOT_FOUND"
    title = "Not found"
    status_code = 404


class ConflictError(TalentScopeError):
    code = "CONFLICT"
    title = "Conflict"
    status_code = 409


class ValidationAppError(TalentScopeError):
    code = "VALIDATION_ERROR"
    title = "Validation error"
    status_code = 400


class UnauthorizedError(TalentScopeError):
    code = "AUTH_REQUIRED"
    title = "Authentication required"
    status_code = 401


class ForbiddenError(TalentScopeError):
    code = "FORBIDDEN"
    title = "Forbidden"
    status_code = 403


class InvalidCredentialsError(TalentScopeError):
    code = "INVALID_CREDENTIALS"
    title = "Invalid credentials"
    status_code = 401

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "아이디 또는 비밀번호를 확인해주세요.")


class CsrfInvalidError(TalentScopeError):
    code = "CSRF_INVALID"
    title = "CSRF token invalid"
    status_code = 403

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "CSRF 검증에 실패했습니다.")


class SessionStoreUnavailableError(TalentScopeError):
    code = "SESSION_STORE_UNAVAILABLE"
    title = "Session store unavailable"
    status_code = 503

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "세션 저장소를 사용할 수 없습니다.")
