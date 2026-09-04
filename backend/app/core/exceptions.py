"""Application exception hierarchy."""


class TalentScopeError(Exception):
    """Base application error."""


class NotFoundError(TalentScopeError):
    """Requested resource was not found."""


class ConflictError(TalentScopeError):
    """Request conflicts with current state."""


class ValidationAppError(TalentScopeError):
    """Domain validation failed."""


class UnauthorizedError(TalentScopeError):
    """Caller is not authenticated."""


class ForbiddenError(TalentScopeError):
    """Caller is authenticated but not allowed."""
