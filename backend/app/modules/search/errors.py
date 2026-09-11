"""Sanitized search-index errors — never leak SQL, search_text, or PII."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.modules.search.schemas import ERROR_MESSAGE_MAX_CHARS


class SearchIndexProcessingError(Exception):
    """Already-safe processing failure for service → task boundary."""


class SearchIndexTaskError(Exception):
    """Sanitized Celery-visible failure (no original exception chain)."""


def safe_search_job_error(exc: BaseException) -> str:
    """Map exceptions to a short, non-sensitive SearchIndexJob.error_message.

    Never stores str(exc) / SQL / bind params / search_text / PII.
    """
    if isinstance(exc, SearchIndexProcessingError):
        text = str(exc).replace('\n', " ").strip()
        if not text:
            return "search index processing failed"
        return text[:ERROR_MESSAGE_MAX_CHARS].rstrip()

    if isinstance(exc, (SQLAlchemyError, DBAPIError)):
        return "search index persistence failed"

    if isinstance(exc, ValueError):
        lower = str(exc).lower()
        if "unsupported" in lower and "action" in lower:
            return "unsupported search index action"
        if "person not found" in lower:
            return "person not found for search index rebuild"
        if "job not found" in lower:
            return "search index job not found"
        if "requires person_id" in lower:
            return "rebuild person requires person_id"

    try:
        from app.ai.providers.errors import AIProviderError, AIResponseValidationError
    except Exception:  # pragma: no cover
        AIProviderError = ()  # type: ignore[assignment,misc]
        AIResponseValidationError = ()  # type: ignore[assignment,misc]

    if AIResponseValidationError and isinstance(exc, AIResponseValidationError):
        msg = str(exc).lower()
        if "dimension" in msg:
            return "embedding dimension mismatch"
        return "embedding response invalid"
    if AIProviderError and isinstance(exc, AIProviderError):
        text = str(exc).replace("\n", " ").strip()
        lower = text.lower()
        if "timeout" in lower:
            return "embedding provider timeout"
        if "http" in lower:
            parts = text.split()
            code = parts[-1] if parts and parts[-1].isdigit() else None
            return (
                f"embedding provider HTTP {code}"
                if code
                else "embedding provider request failed"
            )
        return "embedding provider request failed"

    name = type(exc).__name__
    if "Document" in name or "Builder" in name:
        return "search document build failed"

    return f"{name}: search index processing failed"
