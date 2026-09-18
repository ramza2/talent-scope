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
    """Parse a JSON object from model output (fences / prose tolerant).

    In aggressive mode, also recover the last complete top-level JSON object
    when a model emits reasoning/prose or more than one object.  Qwen-family
    models occasionally return a draft object followed by the final object even
    when instructed to output JSON only.
    """
    if text is None:
        raise AIResponseValidationError("empty AI response")
    raw = text.strip()
    if not raw:
        raise AIResponseValidationError("empty AI response")

    candidates: list[str] = [raw]
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.insert(0, fence.group(1).strip())

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

    if aggressive:
        # Try each candidate independently so a fenced final answer wins over
        # unrelated prose outside the fence.  If multiple complete top-level
        # objects exist, prefer the last one as the model's final answer.
        for candidate in candidates:
            decoded = _scan_top_level_objects(candidate)
            if decoded:
                return decoded[-1]

    raise AIResponseValidationError("failed to parse AI JSON object") from last_error


def _scan_top_level_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    found: list[tuple[int, int, dict[str, Any]]] = []

    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found.append((start, start + consumed, value))

    if not found:
        return []

    # raw_decode also succeeds at nested object starts.  Remove objects fully
    # contained in another decoded object so only top-level answer candidates
    # remain.
    top_level: list[tuple[int, int, dict[str, Any]]] = []
    for item in found:
        start, end, _value = item
        if any(
            other_start < start and end <= other_end
            for other_start, other_end, _other_value in found
        ):
            continue
        top_level.append(item)

    top_level.sort(key=lambda item: item[0])
    return [value for _start, _end, value in top_level]
