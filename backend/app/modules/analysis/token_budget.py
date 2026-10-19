"""Adaptive token budgeting helpers (ratios-driven; no hard-coded context sizes)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.ai.providers.model_capabilities import ModelCapabilities


@dataclass(frozen=True)
class PhaseTokenBudget:
    """Per-phase completion/prompt budget derived from a live context_window."""

    context_window: int
    max_completion_tokens: int
    max_prompt_tokens: int
    safety_tokens: int
    output_ratio: float
    safety_ratio: float
    deployment_id: str | None
    routing_version: int | None


def compute_phase_token_budget(
    capabilities: ModelCapabilities,
    *,
    output_ratio: float,
    safety_ratio: float,
) -> PhaseTokenBudget:
    """Derive phase budget from verified context_window and ratios only."""
    window = int(capabilities.context_window)
    if window <= 0:
        raise ValueError("context_window must be positive")
    out_r = float(output_ratio)
    safety_r = float(safety_ratio)
    if not (0.0 < out_r < 1.0):
        raise ValueError("output_ratio must be in (0, 1)")
    if not (0.0 <= safety_r < 1.0):
        raise ValueError("safety_ratio must be in [0, 1)")
    if out_r + safety_r >= 1.0:
        raise ValueError("output_ratio + safety_ratio must be < 1")

    safety_tokens = max(1, int(math.floor(window * safety_r)))
    max_completion = max(1, int(math.floor(window * out_r)))
    # Leave room for prompt + completion + safety inside the window.
    max_prompt = max(1, window - max_completion - safety_tokens)
    return PhaseTokenBudget(
        context_window=window,
        max_completion_tokens=max_completion,
        max_prompt_tokens=max_prompt,
        safety_tokens=safety_tokens,
        output_ratio=out_r,
        safety_ratio=safety_r,
        deployment_id=capabilities.deployment_id,
        routing_version=capabilities.routing_version,
    )


def estimate_text_tokens(text: str, *, chars_per_token: float) -> int:
    """Conservative token estimate (hint only; never treat as exact).

    No external tokenizer. Larger chars_per_token → lower estimate; keep
    chars_per_token small (e.g. 2.0) so CJK-heavy prompts are over-estimated.
    """
    if not text:
        return 0
    cpt = float(chars_per_token)
    if cpt <= 0:
        cpt = 1.0
    return max(1, int(math.ceil(len(text) / cpt)))


def prompt_fits_budget(
    *,
    system_prompt: str,
    user_prompt: str,
    budget: PhaseTokenBudget,
    chars_per_token: float,
) -> bool:
    """True when conservative prompt estimate fits max_prompt_tokens."""
    est = estimate_text_tokens(
        (system_prompt or "") + (user_prompt or ""),
        chars_per_token=chars_per_token,
    )
    return est <= budget.max_prompt_tokens


_DOCUMENT_SPLIT_RE = re.compile(
    r"(?=\[DOCUMENT(?:\s+[^\]]+)?\]\n)"
)
_PAGE_SPLIT_RE = re.compile(r"(?=\[PAGE\s+\d+\]\n)")


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
    # Header ends at "content:\n"
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
    """Split oversized page/body text into bounded character windows."""
    raw = text or ""
    if max_chars <= 0:
        return [raw] if raw else []
    if len(raw) <= max_chars:
        return [raw] if raw else []
    return [raw[i : i + max_chars] for i in range(0, len(raw), max_chars)]


def split_page_block_text(
    document_block: str, *, max_body_chars: int
) -> list[str]:
    """Re-split a single-page (or header+body) block into smaller body chunks."""
    text = document_block or ""
    marker = "content:\n"
    idx = text.find(marker)
    if idx < 0:
        return split_text_into_bounded_chunks(text, max_chars=max_body_chars)
    header = text[: idx + len(marker)]
    body = text[idx + len(marker) :]
    if len(body) <= max_body_chars:
        return [text]
    pieces = split_text_into_bounded_chunks(body, max_chars=max_body_chars)
    return [f"{header}{piece}" for piece in pieces]


def plan_project_source_chunks(
    document_blocks: str,
    *,
    system_prompt: str,
    code_catalog: str,
    build_user_prompt,
    budget: PhaseTokenBudget,
    chars_per_token: float,
    recovery_retry: bool = False,
) -> list[str]:
    """Plan PROJECT source chunks using conservative fit estimates (hints).

    Returns a single-element list when the full prompt fits; otherwise splits
    document → pages → bounded body chunks until each estimated prompt fits
    or cannot shrink further.
    """

    def _fits(blocks: str) -> bool:
        user = build_user_prompt(
            code_catalog=code_catalog,
            document_blocks=blocks,
            recovery_retry=recovery_retry,
        )
        return prompt_fits_budget(
            system_prompt=system_prompt,
            user_prompt=user,
            budget=budget,
            chars_per_token=chars_per_token,
        )

    if _fits(document_blocks):
        return [document_blocks]

    chunks: list[str] = []
    for doc_block in split_document_blocks(document_blocks) or [document_blocks]:
        if _fits(doc_block):
            chunks.append(doc_block)
            continue
        page_chunks = split_block_by_pages(doc_block)
        if len(page_chunks) <= 1:
            page_chunks = [doc_block]
        for page_block in page_chunks:
            if _fits(page_block):
                chunks.append(page_block)
                continue
            # Shrink body until fit or irreducible.
            max_chars = max(256, len(page_block) // 2)
            pending = [page_block]
            while pending:
                cur = pending.pop(0)
                if _fits(cur) or len(cur) <= 256:
                    chunks.append(cur)
                    continue
                max_chars = max(256, len(cur) // 2)
                parts = split_page_block_text(cur, max_body_chars=max_chars)
                if len(parts) <= 1:
                    chunks.append(cur)
                    continue
                pending.extend(parts)
    return chunks or [document_blocks]
