"""ModelOps ``/v1/token-count`` client (exact chat prompt measurement)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.ai.providers.errors import AIProviderError
from app.ai.providers.model_capabilities import (
    normalize_v1_base_url,
    route_identity,
)
from app.ai.providers.openai_compat import _json_headers
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenCountResult:
    """Verified ModelOps token-count snapshot (not a route lease)."""

    model: str
    prompt_tokens: int
    context_window: int
    status: str
    deployment_id: str | None
    route_id: str | None
    routing_version: int | None

    @property
    def identity(self) -> tuple[str | None, str | None, int | None]:
        return route_identity(
            deployment_id=self.deployment_id,
            route_id=self.route_id,
            routing_version=self.routing_version,
        )


def normalize_token_count_url(base_url: str) -> str:
    """Build ``.../v1/token-count`` from LLM base URL."""
    return f"{normalize_v1_base_url(base_url)}/token-count"


def build_token_count_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool | None,
) -> dict[str, Any]:
    """Build the exact prompt payload used for Chat Completions counting.

    Intentionally omits ``max_completion_tokens`` / ``max_tokens`` / tools —
    generation limits are applied only on Chat after budgeting.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if enable_thinking is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
    return payload


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        iv = int(value)
        return iv if iv > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        iv = int(value.strip())
        return iv if iv > 0 else None
    return None


def _parse_routing_version(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise AIProviderError("token-count invalid routing_version")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise AIProviderError("token-count invalid routing_version")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def count_chat_prompt_tokens(
    settings: Settings,
    *,
    system_prompt: str,
    user_prompt: str,
    enable_thinking: bool | None,
    timeout_seconds: float | None = None,
    log_context: dict[str, Any] | None = None,
) -> TokenCountResult:
    """POST ModelOps token-count for the exact Chat prompt.

    Fail closed on non-VERIFIED / invalid / HTTP error responses.
    Never logs prompt/document bodies.
    """
    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else settings.analysis_token_count_timeout_seconds
    )
    url = normalize_token_count_url(settings.llm_base_url)
    headers = _json_headers(settings.llm_api_key)
    payload = build_token_count_payload(
        model=settings.llm_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        enable_thinking=enable_thinking,
    )
    ctx = {
        **(log_context or {}),
        "provider": "token_count",
        "model": settings.llm_model,
        "enable_thinking": enable_thinking,
        "input_char_count": len(system_prompt or "") + len(user_prompt or ""),
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("token_count timeout context=%s", ctx)
        raise AIProviderError("token-count request timed out") from exc
    except httpx.HTTPError as exc:
        logger.warning("token_count http_error context=%s", ctx)
        raise AIProviderError("token-count request failed") from exc

    status_code = response.status_code
    logger.info(
        "token_count http_status=%s model=%s context=%s",
        status_code,
        settings.llm_model,
        ctx,
    )
    if status_code >= 400:
        raise AIProviderError(f"token-count HTTP {status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise AIProviderError("token-count returned non-JSON") from exc
    if not isinstance(data, dict):
        raise AIProviderError("token-count unexpected JSON shape")

    status = str(data.get("status") or "").strip().upper()
    if status != "VERIFIED":
        raise AIProviderError(
            f"token-count not verified (status={status or 'UNKNOWN'})"
        )

    prompt_tokens = _positive_int(data.get("prompt_tokens"))
    if prompt_tokens is None:
        raise AIProviderError("token-count missing prompt_tokens")
    context_window = _positive_int(data.get("context_window"))
    if context_window is None:
        raise AIProviderError("token-count missing context_window")

    result = TokenCountResult(
        model=str(data.get("model") or settings.llm_model).strip(),
        prompt_tokens=prompt_tokens,
        context_window=context_window,
        status=status,
        deployment_id=_optional_str(data.get("deployment_id")),
        route_id=_optional_str(data.get("route_id")),
        routing_version=_parse_routing_version(data.get("routing_version")),
    )
    logger.info(
        "token_count ok model=%s prompt_tokens=%s context_window=%s "
        "deployment_id=%s route_id=%s routing_version=%s context=%s",
        result.model,
        result.prompt_tokens,
        result.context_window,
        result.deployment_id,
        result.route_id,
        result.routing_version,
        ctx,
    )
    return result
