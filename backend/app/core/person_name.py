"""Person name canonicalization and comparison helpers.

``normalize_person_name`` is for storage/display canonicalization.
``person_name_match_key`` is for equality / duplicate matching only.
"""

from __future__ import annotations

import re

_HANGUL_PERSON_NAME = re.compile(r"[가-힣]{2,6}")


def normalize_person_name(value: str | None) -> str | None:
    """Canonicalize a person name for storage.

    Ordinary Hangul-only names (2–6 syllables) have internal whitespace
    removed. Non-Korean or ambiguous names are only stripped.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    compact = re.sub(r"\s+", "", stripped)
    if _HANGUL_PERSON_NAME.fullmatch(compact):
        return compact
    return stripped


def person_name_match_key(value: str | None) -> str | None:
    """Comparison key: canonicalize, collapse whitespace, casefold.

    Must not be written to DB display/storage fields.
    """
    canonical = normalize_person_name(value)
    if canonical is None:
        return None
    collapsed = re.sub(r"\s+", " ", canonical).casefold()
    return collapsed or None


def is_korean_person_name(value: str | None) -> bool:
    """True when value normalizes to a 2–6 Hangul-only person name."""
    canonical = normalize_person_name(value)
    if canonical is None:
        return False
    return _HANGUL_PERSON_NAME.fullmatch(canonical) is not None
