"""Deterministic career_document_value → months parser.

LLM never computes career_confirmed_months. Backend may derive months from a
short document career expression for a PROFILE review Diff.
"""

from __future__ import annotations

import re

# Safe upper bound for a total career duration (not a calendar year).
# Values like ``2012년`` are rejected as unrealistic career lengths.
MAX_CAREER_YEARS = 60
MAX_CAREER_MONTHS = MAX_CAREER_YEARS * 12

# Reject decimal fragments like "11.5년" by requiring the number is not
# preceded by a digit or decimal point.
_YEARS_AND_MONTHS = re.compile(
    r"(?<![\d.])(?P<years>\d+)\s*년\s*(?P<months>\d+)\s*개월"
)
_YEARS_ONLY = re.compile(r"(?<![\d.])(?P<years>\d+)\s*년")
_MONTHS_ONLY = re.compile(r"(?<![\d.])(?P<months>\d+)\s*개월")

# Approximate / range markers that must not be converted to exact months.
_APPROX_OR_RANGE_MARKER = re.compile(
    r"(약|이상|미만|내외|정도|최소|최대|"
    r"(?<![\d.])\d+\s*(?:년|개월)\s*\+)"
)


def parse_career_document_months(value: str | None) -> int | None:
    """Parse a short Korean total-career expression into whole months.

    Supported examples:
    - ``11년 10개월`` → 142
    - ``16년`` → 192
    - ``10개월`` → 10
    - ``기술경력 16년`` → 192
    - ``SW기술자 경력 12년 4개월`` → 148

    Approximate / range / unrealistic expressions return ``None``. Never guesses.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _has_approx_or_range_marker(text):
        return None

    match = _YEARS_AND_MONTHS.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        years = int(match.group("years"))
        months = int(match.group("months"))
        if months >= 12:
            return None
        if not _years_within_bound(years):
            return None
        total = years * 12 + months
        return total if _months_within_bound(total) else None

    match = _YEARS_ONLY.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        # Reject strings that also contain a months token outside this match
        # (e.g. "16년 및 10개월" without the combined pattern).
        if _MONTHS_ONLY.search(text):
            return None
        years = int(match.group("years"))
        if not _years_within_bound(years):
            return None
        return years * 12

    match = _MONTHS_ONLY.search(text)
    if match is not None:
        if _has_other_duration(text, match.span()):
            return None
        months = int(match.group("months"))
        return months if _months_within_bound(months) else None

    return None


def _has_approx_or_range_marker(text: str) -> bool:
    return _APPROX_OR_RANGE_MARKER.search(text) is not None


def _years_within_bound(years: int) -> bool:
    return 0 <= years <= MAX_CAREER_YEARS


def _months_within_bound(months: int) -> bool:
    return 0 <= months <= MAX_CAREER_MONTHS


def _has_other_duration(text: str, span: tuple[int, int]) -> bool:
    """True when another year/month duration exists outside ``span``."""
    remainder = f"{text[: span[0]]} {text[span[1] :]}"
    return bool(
        _YEARS_AND_MONTHS.search(remainder)
        or _YEARS_ONLY.search(remainder)
        or _MONTHS_ONLY.search(remainder)
    )
