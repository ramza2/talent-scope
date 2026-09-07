"""JSON object extraction helpers for LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.providers.errors import AIResponseValidationError

_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)


def parse_json_object(text: str, *, aggressive: bool = False) -> dict[str, Any]:
    """Parse a JSON object from model output (fences / prose tolerant)."""
    if text is None:
        raise AIResponseValidationError("empty AI response")
    raw = text.strip()
    if not raw:
        raise AIResponseValidationError("empty AI response")

    candidates: list[str] = [raw]
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.insert(0, fence.group(1).strip())

    if aggressive:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            candidates.append(raw[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
        last_error = AIResponseValidationError("AI JSON root is not an object")

    raise AIResponseValidationError("failed to parse AI JSON object") from last_error
