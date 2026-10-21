"""ModelOps token-count based phase budgets (no local token estimators)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.ai.providers.model_capabilities import ModelCapabilities
from app.ai.providers.token_count import TokenCountResult


@dataclass(frozen=True)
class PromptChatBudget:
    """Exact prompt + ratio reserves checked against context_window."""

    context_window: int
    prompt_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    max_completion_tokens: int
    fits: bool
    deployment_id: str | None
    route_id: str | None
    routing_version: int | None
    enable_thinking: bool | None


def _validate_ratios(*, output_ratio: float, safety_ratio: float) -> None:
    out_r = float(output_ratio)
    safety_r = float(safety_ratio)
    if not (0.0 < out_r < 1.0):
        raise ValueError("output_ratio must be in (0, 1)")
    if not (0.0 <= safety_r < 1.0):
        raise ValueError("safety_ratio must be in [0, 1)")
    if out_r + safety_r >= 1.0:
        raise ValueError("output_ratio + safety_ratio must be < 1")


def compute_prompt_chat_budget(
    count: TokenCountResult,
    *,
    output_reserve_ratio: float,
    safety_margin_ratio: float,
    enable_thinking: bool | None,
) -> PromptChatBudget:
    """Budget Chat completion from exact token-count + ratio reserves.

    Requires:
    ``prompt_tokens + output_reserve + safety_margin <= context_window``.
    Thinking ON uses the same enable_thinking flag as Chat; thinking tokens
    consume the completion reserve (max_completion_tokens).
    """
    window = int(count.context_window)
    prompt_tokens = int(count.prompt_tokens)
    if window <= 0 or prompt_tokens <= 0:
        raise ValueError("token-count window/prompt_tokens must be positive")
    _validate_ratios(
        output_ratio=output_reserve_ratio, safety_ratio=safety_margin_ratio
    )
    output_reserve = max(
        1, int(math.floor(window * float(output_reserve_ratio)))
    )
    safety_margin = max(
        1, int(math.floor(window * float(safety_margin_ratio)))
    )
    fits = (prompt_tokens + output_reserve + safety_margin) <= window
    # When fitting, request the full ratio reserve. When not, report a
    # residual completion room for diagnostics only (caller must split).
    residual = window - prompt_tokens - safety_margin
    max_completion = output_reserve if fits else max(0, residual)
    return PromptChatBudget(
        context_window=window,
        prompt_tokens=prompt_tokens,
        output_reserve_tokens=output_reserve,
        safety_margin_tokens=safety_margin,
        max_completion_tokens=max_completion,
        fits=fits,
        deployment_id=count.deployment_id,
        route_id=count.route_id,
        routing_version=count.routing_version,
        enable_thinking=enable_thinking,
    )


def token_count_route_changed(
    previous: TokenCountResult | ModelCapabilities | PromptChatBudget | None,
    current: TokenCountResult | ModelCapabilities | PromptChatBudget,
) -> bool:
    """True when observed ModelOps route identity fields differ."""
    if previous is None:
        return True
    return (
        previous.deployment_id != current.deployment_id
        or getattr(previous, "route_id", None)
        != getattr(current, "route_id", None)
        or previous.routing_version != current.routing_version
        or previous.context_window != current.context_window
    )


_DOCUMENT_SPLIT_RE = re.compile(
    r"(?=\[DOCUMENT(?:\s+[^\]]+)?\]\n)"
)
_PAGE_SPLIT_RE = re.compile(r"(?=\[PAGE\s+\d+\]\n)")
_LEADING_PAGE_MARKER_RE = re.compile(r"^(\[PAGE\s+\d+\]\n)")


def split_document_blocks(document_blocks: str) -> list[str]:
    """Split prompt source into per-document blocks (preserves headers)."""
    text = document_blocks or ""
    if not text.strip():
        return []
    parts = [p for p in _DOCUMENT_SPLIT_RE.split(text) if p and p.strip()]
    if len(parts) <= 1:
        return [text] if text.strip() else []
    return parts


def split_block_by_pages(document_block: str) -> list[str]:
    """Split one document block into page-range chunks sharing the header."""
    text = document_block or ""
    if not text.strip():
        return []
    marker = "content:\n"
    idx = text.find(marker)
    if idx < 0:
        return [text]
    header = text[: idx + len(marker)]
    body = text[idx + len(marker) :]
    pages = [p for p in _PAGE_SPLIT_RE.split(body) if p and p.strip()]
    if len(pages) <= 1:
        return [text]
    return [f"{header}{page}" for page in pages]


def split_text_into_bounded_chunks(text: str, *, max_chars: int) -> list[str]:
    """Structural body split only — fitness must be rechecked via token-count."""
    raw = text or ""
    if max_chars <= 0:
        return [raw] if raw else []
    if len(raw) <= max_chars:
        return [raw] if raw else []
    return [raw[i : i + max_chars] for i in range(0, len(raw), max_chars)]


def split_page_block_text(
    document_block: str, *, max_body_chars: int
) -> list[str]:
    """Re-split a single-page block into smaller body chunks.

    Preserves the document header and the original ``[PAGE n]`` marker on
    every subchunk. Never invents page numbers — only reuses a leading
    marker already present in the source body.
    """
    text = document_block or ""
    marker = "content:\n"
    idx = text.find(marker)
    if idx < 0:
        return split_text_into_bounded_chunks(text, max_chars=max_body_chars)
    header = text[: idx + len(marker)]
    body = text[idx + len(marker) :]
    page_marker = ""
    page_match = _LEADING_PAGE_MARKER_RE.match(body)
    if page_match:
        page_marker = page_match.group(1)
        page_body = body[len(page_marker) :]
    else:
        page_body = body
    if len(page_body) <= max_body_chars:
        return [text]
    pieces = split_text_into_bounded_chunks(page_body, max_chars=max_body_chars)
    return [f"{header}{page_marker}{piece}" for piece in pieces]


def plan_phase_source_chunks(
    document_blocks: str,
    *,
    budget_for_blocks,
) -> list[str]:
    """Plan CORE/PROJECT chunks down to document/page level only.

    ``budget_for_blocks(blocks) -> PromptChatBudget`` must use /v1/token-count
    for fit. Over-budget pages are returned as-is so the extract path can
    deepen body subchunks under phase split depth/call caps — the planner
    must not recursively fan out unbounded token-count calls.
    """
    root_budget = budget_for_blocks(document_blocks)
    if root_budget.fits:
        return [document_blocks]

    chunks: list[str] = []
    for doc_block in split_document_blocks(document_blocks) or [document_blocks]:
        doc_budget = budget_for_blocks(doc_block)
        if doc_budget.fits:
            chunks.append(doc_block)
            continue
        page_chunks = split_block_by_pages(doc_block)
        if len(page_chunks) <= 1:
            # Single page/body still over budget — defer deep split to extract.
            chunks.append(doc_block)
            continue
        # Page is the planner leaf; body deepening is extract-bounded.
        chunks.extend(page_chunks)
    return chunks or [document_blocks]


# Backward-compatible alias used by the PROJECT adaptive path.
plan_project_source_chunks = plan_phase_source_chunks
