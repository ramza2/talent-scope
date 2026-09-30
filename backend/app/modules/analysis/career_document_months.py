"""Deterministic career_document_value → months parser.

LLM never computes career_confirmed_months. Backend may derive months from a
short document career expression for a PROFILE review Diff.
"""

from __future__ import annotations

import re

# Reject decimal fragments like "11.5년" by requiring the number is not
# preceded by a digit or decimal point.
_YEARS_AND_MONTHS = re.compile(
    r"(?<![\d.])(?P<years>\d+)\s*년\s*(?P<months>\d+)\s*개월"
)
_YEARS_ONLY = re.compile(r"(?<![\d.])(?P<years>\d+)\s*년")
_MONTHS_ONLY = re.compile(r"(?<![\d.])(?P<months>\d+)\s*개월")


def parse_career_document_months(value: str | None) -> int | None:
    """Parse a short Korean total-career expression into whole months.

    Supported examples:
    - ``11년 10개월`` → 142
    - ``16년`` → 192
    - ``10개월`` → 10
    - ``기술경력 16년`` → 192
    - ``SW기술자 경력 12년 4개월`` → 148

    Ambiguous / unparseable strings return ``None``. Never guesses.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    match = _YEARS_AND_MONTHS.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        years = int(match.group("years"))
        months = int(match.group("months"))
        if months >= 12:
            return None
        total = years * 12 + months
        return total if total >= 0 else None

    match = _YEARS_ONLY.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        # Reject strings that also contain a months token outside this match
        # (e.g. "16년 및 10개월" without the combined pattern).
        if _MONTHS_ONLY.search(text):
            return None
        years = int(match.group("years"))
        return years * 12 if years >= 0 else None

    match = _MONTHS_ONLY.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        months = int(match.group("months"))
        return months if months >= 0 else None

    return None


def _has_other_duration(text: str, span: tuple[int, int]) -> bool:
    """True when another year/month duration exists outside ``span``."""
    remainder = f"{text[: span[0]]} {text[span[1] :]}"
    return bool(
        _YEARS_AND_MONTHS.search(remainder)
        or _YEARS_ONLY.search(remainder)
        or _MONTHS_ONLY.search(remainder)
    )
