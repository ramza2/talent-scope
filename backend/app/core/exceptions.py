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


class CodeAlreadyExistsError(ConflictError):
    code = "CODE_ALREADY_EXISTS"
    title = "Code already exists"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "이미 존재하는 코드입니다.")


class InvalidCodeTypeError(ValidationAppError):
    code = "INVALID_CODE_TYPE"
    title = "Invalid code type"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "허용되지 않은 코드 유형입니다.")


class InvalidCodeParentError(ValidationAppError):
    code = "INVALID_CODE_PARENT"
    title = "Invalid code parent"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "상위 코드가 올바르지 않습니다.")


class CodeHierarchyCycleError(ValidationAppError):
    code = "CODE_HIERARCHY_CYCLE"
    title = "Code hierarchy cycle"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "코드 계층에 순환이 발생합니다.")


class UserLoginIdExistsError(ConflictError):
    code = "USER_LOGIN_ID_EXISTS"
    title = "Login ID already exists"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "이미 사용 중인 로그인 ID입니다.")


class InvalidUserRoleError(ValidationAppError):
    code = "INVALID_USER_ROLE"
    title = "Invalid user role"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "허용되지 않은 사용자 역할입니다.")


class InvalidUserStatusError(ValidationAppError):
    code = "INVALID_USER_STATUS"
    title = "Invalid user status"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "허용되지 않은 사용자 상태입니다.")


class CannotDeactivateSelfError(ValidationAppError):
    code = "CANNOT_DEACTIVATE_SELF"
    title = "Cannot deactivate self"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "자신의 계정을 비활성화할 수 없습니다.")


class LastActiveAdminRequiredError(ValidationAppError):
    code = "LAST_ACTIVE_ADMIN_REQUIRED"
    title = "Last active admin required"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "마지막 활성 관리자 계정은 변경할 수 없습니다.")


class ProfileVersionConflictError(ConflictError):
    code = "PROFILE_VERSION_CONFLICT"
    title = "Profile version conflict"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            detail or "프로필이 다른 작업에 의해 먼저 수정되었습니다. 최신 정보를 다시 불러오세요."
        )


class InvalidPersonStatusError(ValidationAppError):
    code = "INVALID_PERSON_STATUS"
    title = "Invalid person status"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "허용되지 않은 인력 상태입니다.")


class InvalidTechnicalGradeError(ValidationAppError):
    code = "INVALID_TECHNICAL_GRADE"
    title = "Invalid technical grade"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "허용되지 않은 기술등급입니다.")


class InvalidJobCodeError(ValidationAppError):
    code = "INVALID_JOB_CODE"
    title = "Invalid job code"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "유효하지 않은 직무 코드입니다.")


class InvalidTechCodeError(ValidationAppError):
    code = "INVALID_TECH_CODE"
    title = "Invalid tech code"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "유효하지 않은 기술 코드입니다.")


class InvalidExpCodeError(ValidationAppError):
    code = "INVALID_EXP_CODE"
    title = "Invalid expertise code"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or "유효하지 않은 전문분야 코드입니다.")
