"""Deterministic hybrid ranking helpers (RRF + preferred contribution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

HYBRID_RRF_K = 60

WEIGHT_KEYWORD = 0.45
WEIGHT_SEMANTIC = 0.45
WEIGHT_PREFERRED = 0.10

MIN_CHANNEL_CANDIDATE_ITEMS = 500
MAX_CHANNEL_CANDIDATE_ITEMS = 5000

# Keep low enough for short Korean/tech tokens; central constant for tests.
KEYWORD_TRIGRAM_THRESHOLD = 0.25

SKILL_DISPLAY_CAP = 10
EXPERTISE_DISPLAY_CAP = 10


@dataclass
class ChannelHit:
    person_id: UUID
    rank: int  # 1-based among persons in this channel
    item_id: UUID | None = None
    object_type: str | None = None
    object_id: UUID | None = None
    raw_score: float = 0.0
    source_weight: float = 1.0


@dataclass
class MergedCandidate:
    person_id: UUID
    keyword_hit: ChannelHit | None = None
    semantic_hit: ChannelHit | None = None
    preferred_match_ratio: float = 0.0
    relevance: float = 0.0
    score: int = 0
    sort_keys: dict[str, Any] = field(default_factory=dict)


def channel_candidate_limit(*, page: int, page_size: int) -> int:
    depth = max(MIN_CHANNEL_CANDIDATE_ITEMS, int(page) * int(page_size) * 10)
    return min(MAX_CHANNEL_CANDIDATE_ITEMS, depth)


def rrf_score(rank: int, *, k: int = HYBRID_RRF_K) -> float:
    """Reciprocal Rank Fusion contribution for a 1-based rank."""
    if rank < 1:
        return 0.0
    return (k + 1) / (k + rank)


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def cosine_similarity_from_distance(distance: float) -> float:
    return clamp01(1.0 - float(distance))


def compute_hybrid_relevance(
    *,
    keyword_rank: int | None,
    semantic_rank: int | None,
    preferred_match_ratio: float,
    preferred_present: bool,
) -> float:
    """Normalize existing channel weights and return relevance in [0, 1]."""
    parts: list[tuple[float, float]] = []
    if keyword_rank is not None:
        parts.append((WEIGHT_KEYWORD, rrf_score(keyword_rank)))
    if semantic_rank is not None:
        parts.append((WEIGHT_SEMANTIC, rrf_score(semantic_rank)))
    if preferred_present:
        parts.append((WEIGHT_PREFERRED, clamp01(preferred_match_ratio)))

    if not parts:
        return 0.0

    weight_sum = sum(w for w, _ in parts)
    if weight_sum <= 0:
        return 0.0
    return sum((w / weight_sum) * v for w, v in parts)


def relevance_to_score(relevance: float) -> int:
    """Map [0,1] relevance to integer score 0..100 (not a probability)."""
    return int(round(clamp01(relevance) * 100))


def merge_channel_hits(
    *,
    keyword_hits: list[ChannelHit],
    semantic_hits: list[ChannelHit],
) -> dict[UUID, MergedCandidate]:
    merged: dict[UUID, MergedCandidate] = {}
    for hit in keyword_hits:
        cand = merged.setdefault(hit.person_id, MergedCandidate(person_id=hit.person_id))
        cand.keyword_hit = hit
    for hit in semantic_hits:
        cand = merged.setdefault(hit.person_id, MergedCandidate(person_id=hit.person_id))
        cand.semantic_hit = hit
    return merged


# ---------------------------------------------------------------------------
# rank-v2: retrieval RRF + business relevance (project / recency / required)
# ---------------------------------------------------------------------------

SEARCH_RANKING_POLICY_VERSION = "rank-v2"

WEIGHT_REQUIRED = 0.20
WEIGHT_RETRIEVAL = 0.45
WEIGHT_PREFERRED_V2 = 0.10
WEIGHT_PROJECT = 0.20
WEIGHT_RECENCY = 0.05

# Per-project channel mix (active components only, then renormalize)
PROJECT_WEIGHT_STRUCTURED = 0.60
PROJECT_WEIGHT_KEYWORD = 0.20
PROJECT_WEIGHT_SEMANTIC = 0.20

# Person-level project aggregation
PROJECT_BEST_WEIGHT = 0.70
PROJECT_COUNT_WEIGHT = 0.20
PROJECT_DURATION_WEIGHT = 0.10
PROJECT_COUNT_CAP = 3
PROJECT_DURATION_MONTHS_CAP = 36

EXPERTISE_EXPLICIT_FACTOR = 1.0
EXPERTISE_INFERRED_FACTOR = 0.70

TOP_PROJECTS_LIMIT = 3
SEARCH_EVIDENCE_LIMIT = 8
SEARCH_EVIDENCE_SNIPPET_MAX_CHARS = 240


def compute_retrieval_relevance(
    *,
    keyword_rank: int | None,
    semantic_rank: int | None,
) -> float | None:
    """RRF-only retrieval relevance in [0, 1], or None when inactive."""
    parts: list[float] = []
    if keyword_rank is not None:
        parts.append(rrf_score(keyword_rank))
    if semantic_rank is not None:
        parts.append(rrf_score(semantic_rank))
    if not parts:
        return None
    return sum(parts) / len(parts)


def normalize_weighted_parts(parts: list[tuple[float, float]]) -> float:
    """Normalize active (weight, value) parts to a [0, 1] score."""
    active = [(w, v) for w, v in parts if w > 0]
    if not active:
        return 0.0
    weight_sum = sum(w for w, _ in active)
    if weight_sum <= 0:
        return 0.0
    return sum((w / weight_sum) * clamp01(v) for w, v in active)


def compute_final_relevance(
    *,
    required_score: float | None,
    retrieval_score: float | None,
    preferred_score: float | None,
    project_score: float | None,
    recency_score: float | None,
) -> float:
    """rank-v2 final relevance. Inactive components must be None (not 0.0)."""
    parts: list[tuple[float, float]] = []
    if required_score is not None:
        parts.append((WEIGHT_REQUIRED, required_score))
    if retrieval_score is not None:
        parts.append((WEIGHT_RETRIEVAL, retrieval_score))
    if preferred_score is not None:
        parts.append((WEIGHT_PREFERRED_V2, preferred_score))
    if project_score is not None:
        parts.append((WEIGHT_PROJECT, project_score))
    if recency_score is not None:
        parts.append((WEIGHT_RECENCY, recency_score))
    if not parts:
        return 0.0
    return normalize_weighted_parts(parts)
