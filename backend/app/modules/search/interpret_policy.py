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


# Nested allowlists for recursive strict validation (interpret-only).
# /search/people schemas intentionally keep extra="ignore" for API compat;
# interpret LLM/previous_query payloads must not silently drop unknown keys.
REQUIRED_BLOCK_KEYS = frozenset(
    {
        "jobs",
        "skills",
        "expertise",
        "business_domains",
        "customer_types",
        "grade",
        "career",
        "affiliations",
        "certifications",
        "project_keywords",
    }
)
PREFERRED_BLOCK_KEYS = frozenset(
    {
        "jobs",
        "skills",
        "expertise",
        "business_domains",
        "customer_types",
    }
)
GRADE_KEYS = frozenset({"values"})
CAREER_KEYS = frozenset({"min_months", "max_months"})
LLM_ROOT_KEYS = frozenset(
    {
        "required",
        "preferred",
        "skill_match_mode",
        "semantic_query",
        "keyword_query",
        "sort",
        "assumptions",
    }
)
INTERPRET_DATA_ROOT_KEYS = LLM_ROOT_KEYS | frozenset({"query_version"})


def _forbid_unknown_keys(
    obj: dict[str, Any], allowed: frozenset[str], *, path: str
) -> None:
    unknown = sorted(set(obj.keys()) - allowed)
    if unknown:
        raise ValueError(f"unknown fields at {path}: {', '.join(unknown)}")


def assert_strict_interpret_payload(
    payload: Any, *, include_query_version: bool
) -> None:
    """Recursively reject unknown nested keys before nested Pydantic models run.

    Raises ValueError on any non-allowlisted key (no silent drop).
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    root_allowed = INTERPRET_DATA_ROOT_KEYS if include_query_version else LLM_ROOT_KEYS
    _forbid_unknown_keys(payload, root_allowed, path="root")

    required = payload.get("required")
    if required is not None:
        if not isinstance(required, dict):
            raise ValueError("required must be an object")
        _forbid_unknown_keys(required, REQUIRED_BLOCK_KEYS, path="required")
        grade = required.get("grade")
        if grade is not None:
            if not isinstance(grade, dict):
                raise ValueError("required.grade must be an object")
            _forbid_unknown_keys(grade, GRADE_KEYS, path="required.grade")
        career = required.get("career")
        if career is not None:
            if not isinstance(career, dict):
                raise ValueError("required.career must be an object")
            _forbid_unknown_keys(career, CAREER_KEYS, path="required.career")

    preferred = payload.get("preferred")
    if preferred is not None:
        if not isinstance(preferred, dict):
            raise ValueError("preferred must be an object")
        _forbid_unknown_keys(preferred, PREFERRED_BLOCK_KEYS, path="preferred")
