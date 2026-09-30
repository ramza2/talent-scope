"""AI provider exceptions — never include API keys or raw provider bodies."""

from __future__ import annotations

from typing import Any


class AIProviderError(Exception):
    """Normalized failure calling an external AI provider."""

    def __init__(self, message: str = "AI provider request failed") -> None:
        super().__init__(message)


class AIResponseValidationError(AIProviderError):
    """Provider returned a response that could not be validated."""

    def __init__(self, message: str = "AI response validation failed") -> None:
        super().__init__(message)


class AIResponseTruncatedError(AIProviderError):
    """Chat completion finished with finish_reason=length (incomplete output).

    Must not be aggressively JSON-repaired into a nested object treated as a
    root Candidate. ``meta`` may carry safe usage/finish diagnostics only —
    never response content.
    """

    def __init__(
        self,
        message: str = "AI response truncated (finish_reason=length)",
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.meta = dict(meta or {})
