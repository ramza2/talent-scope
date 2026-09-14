"""Constants and pure helpers for search interpret (NL → Query JSON)."""

from __future__ import annotations

from typing import Any

SEARCH_INTERPRET_PROMPT_VERSION = "search-interpret-v1"
SEARCH_QUERY_VERSION = "1.0"

SEARCH_INTERPRET_MAX_TEXT_CHARS = 2000
SEARCH_INTERPRET_MAX_CODE_CONTEXT_CHARS = 24000
SEARCH_INTERPRET_MAX_ASSUMPTIONS = 10
SEARCH_INTERPRET_MAX_ASSUMPTION_CHARS = 500

# Fixed catalog type order (not alphabetical).
INTERPRET_CODE_TYPES: tuple[str, ...] = (
    "JOB",
    "TECH",
    "EXP",
    "BIZ",
    "CUSTOMER_TYPE",
)

CODE_FIELD_EXPECTED_TYPE: dict[str, str] = {
    "jobs": "JOB",
    "skills": "TECH",
    "expertise": "EXP",
    "business_domains": "BIZ",
    "customer_types": "CUSTOMER_TYPE",
}

GRADE_RANGE_HINTS = {
    "BEGINNER": "초급",
    "INTERMEDIATE": "중급",
    "ADVANCED": "고급",
    "EXPERT": "특급",
    "UNKNOWN": "등급 미상(명시적일 때만)",
}


def normalize_assumptions(raw: list[str] | None) -> list[str]:
    """Trim, drop blanks, dedupe (casefold), bound count/length."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item).split()).strip()
        if not text:
            continue
        if len(text) > SEARCH_INTERPRET_MAX_ASSUMPTION_CHARS:
            text = text[:SEARCH_INTERPRET_MAX_ASSUMPTION_CHARS].rstrip()
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= SEARCH_INTERPRET_MAX_ASSUMPTIONS:
            break
    return out


def executable_query_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Strip interpret-only fields for SearchPeopleRequest validation."""
    return {
        "required": data.get("required") or {},
        "preferred": data.get("preferred") or {},
        "skill_match_mode": data.get("skill_match_mode", "ANY"),
        "semantic_query": data.get("semantic_query"),
        "keyword_query": data.get("keyword_query"),
        "sort": data.get("sort", "RELEVANCE"),
    }


def collect_codes_from_query_dict(data: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for block_name in ("required", "preferred"):
        block = data.get(block_name) or {}
        if not isinstance(block, dict):
            continue
        for field in CODE_FIELD_EXPECTED_TYPE:
            values = block.get(field) or []
            if isinstance(values, list):
                for v in values:
                    if isinstance(v, str) and v.strip():
                        codes.add(v.strip())
    return codes
