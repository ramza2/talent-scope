"""AI provider exceptions — never include API keys or raw provider bodies."""

from __future__ import annotations


class AIProviderError(Exception):
    """Normalized failure calling an external AI provider."""

    def __init__(self, message: str = "AI provider request failed") -> None:
        super().__init__(message)


class AIResponseValidationError(AIProviderError):
    """Provider returned a response that could not be validated."""

    def __init__(self, message: str = "AI response validation failed") -> None:
        super().__init__(message)
