"""ModelOps model capabilities lookup (no stale global cache)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.ai.providers.errors import AIProviderError
from app.ai.providers.openai_compat import _json_headers
from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCapabilities:
    """Verified ModelOps capability snapshot for one analysis attempt."""

    model_id: str
    context_window: int
    supports_thinking: bool
    supports_tools: bool
    deployment_id: str | None
    route_id: str | None
    routing_version: int | None
    status: str


def normalize_v1_base_url(base_url: str) -> str:
    """Normalize provider base URL to an ``.../v1`` prefix."""
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        raise AIProviderError("AI base URL이 비어 있습니다.")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise AIProviderError("AI base URL scheme이 올바르지 않습니다.")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        raw = raw[: -len("/chat/completions")].rstrip("/")
        path = urlparse(raw).path.rstrip("/")
    if path.endswith("/token-count"):
        raw = raw[: -len("/token-count")].rstrip("/")
        path = urlparse(raw).path.rstrip("/")
    if path.endswith("/embeddings"):
        raw = raw[: -len("/embeddings")].rstrip("/")
        path = urlparse(raw).path.rstrip("/")
    if not path.endswith("/v1"):
        raw = f"{raw}/v1"
    return raw


def normalize_capabilities_url(base_url: str, model: str) -> str:
    """Build ``.../v1/models/{model}/capabilities`` from LLM base URL."""
    v1 = normalize_v1_base_url(base_url)
    model_seg = quote(str(model or "").strip(), safe="")
    if not model_seg:
        raise AIProviderError("LLM model id가 비어 있습니다.")
    return f"{v1}/models/{model_seg}/capabilities"


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
        raise AIProviderError("model capabilities invalid routing_version")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise AIProviderError("model capabilities invalid routing_version")


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fetch_model_capabilities(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
) -> ModelCapabilities:
    """GET ModelOps capabilities. Fail closed unless status=VERIFIED.

    Does not cache results — callers must re-fetch when route identity changes.
    Never logs API keys or response bodies.
    """
    timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else settings.analysis_capability_timeout_seconds
    )
    url = normalize_capabilities_url(settings.llm_base_url, settings.llm_model)
    headers = _json_headers(settings.llm_api_key)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning(
            "model_capabilities timeout model=%s", settings.llm_model
        )
        raise AIProviderError("model capabilities request timed out") from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "model_capabilities http_error model=%s", settings.llm_model
        )
        raise AIProviderError("model capabilities request failed") from exc

    if response.status_code >= 400:
        logger.warning(
            "model_capabilities http_status=%s model=%s",
            response.status_code,
            settings.llm_model,
        )
        raise AIProviderError(
            f"model capabilities HTTP {response.status_code}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AIProviderError("model capabilities returned non-JSON") from exc
    if not isinstance(data, dict):
        raise AIProviderError("model capabilities unexpected JSON shape")

    status = str(data.get("status") or "").strip().upper()
    if status != "VERIFIED":
        logger.warning(
            "model_capabilities not verified model=%s status=%s",
            settings.llm_model,
            status or "UNKNOWN",
        )
        raise AIProviderError(
            f"model capabilities not verified (status={status or 'UNKNOWN'})"
        )

    context_window = _positive_int(data.get("context_window"))
    if context_window is None:
        raise AIProviderError("model capabilities missing context_window")

    try:
        routing_version = _parse_routing_version(data.get("routing_version"))
    except AIProviderError:
        raise

    caps = ModelCapabilities(
        model_id=str(data.get("id") or settings.llm_model).strip(),
        context_window=context_window,
        supports_thinking=bool(data.get("supports_thinking")),
        supports_tools=bool(data.get("supports_tools")),
        deployment_id=_optional_str(data.get("deployment_id")),
        route_id=_optional_str(data.get("route_id")),
        routing_version=routing_version,
        status=status,
    )
    logger.info(
        "model_capabilities ok model=%s context_window=%s "
        "deployment_id=%s route_id=%s routing_version=%s",
        caps.model_id,
        caps.context_window,
        caps.deployment_id,
        caps.route_id,
        caps.routing_version,
    )
    return caps


def route_identity(
    *,
    deployment_id: str | None,
    route_id: str | None,
    routing_version: int | None,
) -> tuple[str | None, str | None, int | None]:
    return (deployment_id, route_id, routing_version)


def capabilities_route_changed(
    previous: ModelCapabilities | None, current: ModelCapabilities
) -> bool:
    """True when deployment/route/routing_version/context differs."""
    if previous is None:
        return True
    return (
        previous.deployment_id != current.deployment_id
        or previous.route_id != current.route_id
        or previous.routing_version != current.routing_version
        or previous.context_window != current.context_window
    )
