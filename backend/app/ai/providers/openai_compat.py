"""Shared OpenAI-compatible HTTP helpers."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.providers.errors import AIProviderError

logger = logging.getLogger(__name__)


def normalize_chat_completions_url(base_url: str) -> str:
    """Normalize provider base URL to ``.../v1/chat/completions``."""
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        raise AIProviderError("AI base URL이 비어 있습니다.")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise AIProviderError("AI base URL scheme이 올바르지 않습니다.")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        return raw
    if path.endswith("/v1"):
        return f"{raw}/chat/completions"
    # Treat bare host or custom prefix as needing /v1/chat/completions.
    return f"{raw}/v1/chat/completions"


def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    log_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST chat completions. Never logs API keys or full prompt/response bodies."""
    url = normalize_chat_completions_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    ctx = dict(log_context or {})
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "ai_request timeout model=%s elapsed_ms=%s context=%s",
            payload.get("model"),
            elapsed_ms,
            ctx,
        )
        raise AIProviderError("AI provider request timed out") from exc
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "ai_request http_error model=%s elapsed_ms=%s context=%s",
            payload.get("model"),
            elapsed_ms,
            ctx,
        )
        raise AIProviderError("AI provider request failed") from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    status = response.status_code
    logger.info(
        "ai_request model=%s http_status=%s elapsed_ms=%s context=%s",
        payload.get("model"),
        status,
        elapsed_ms,
        ctx,
    )
    if status >= 400:
        # Do not include response body (may contain sensitive prompt echoes).
        raise AIProviderError(f"AI provider HTTP {status}")

    try:
        data = response.json()
    except ValueError as exc:
        raise AIProviderError("AI provider returned non-JSON response") from exc
    if not isinstance(data, dict):
        raise AIProviderError("AI provider returned unexpected JSON shape")
    return data


def extract_message_content(data: dict[str, Any]) -> str:
    """Extract assistant message content from an OpenAI-compatible response."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIProviderError("AI provider response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise AIProviderError("AI provider response missing message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Some VLM runtimes return content parts.
        texts: list[str] = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        if texts:
            return "\n".join(texts)
    raise AIProviderError("AI provider response missing text content")
