"""Confirmed date normalization for Analysis Confirm (Candidate strings → Date)."""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any, Literal

BoundKind = Literal["start", "end"]

_DATE_RE = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2}))?)?$"
)


def parse_partial_date(value: Any) -> tuple[int, int | None, int | None] | None:
    """Parse YYYY / YYYY-MM / YYYY-MM-DD. Returns None for empty; raises ValueError if illegal."""
    if value is None:
        return None
    if isinstance(value, date):
        return (value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text.replace("/", "-")
    match = _DATE_RE.fullmatch(text)
    if not match:
        raise ValueError(f"invalid date: {value!r}")
    year = int(match.group("y"))
    if year < 1900 or year > 2100:
        raise ValueError(f"invalid year: {value!r}")
    month_s = match.group("m")
    day_s = match.group("d")
    month = int(month_s) if month_s else None
    day = int(day_s) if day_s else None
    if month is not None and (month < 1 or month > 12):
        raise ValueError(f"invalid month: {value!r}")
    if day is not None:
        if month is None:
            raise ValueError(f"invalid date: {value!r}")
        last = calendar.monthrange(year, month)[1]
        if day < 1 or day > last:
            raise ValueError(f"invalid day: {value!r}")
    return (year, month, day)


def normalize_confirmed_date(value: Any, *, bound: BoundKind) -> date | None:
    """Convert partial date string to Confirmed Date using start/end bound rules."""
    parts = parse_partial_date(value)
    if parts is None:
        return None
    year, month, day = parts
    if bound == "start":
        if month is None:
            return date(year, 1, 1)
        if day is None:
            return date(year, month, 1)
        return date(year, month, day)
    # end bound
    if month is None:
        return date(year, 12, 31)
    if day is None:
        return date(year, month, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def assert_date_order(
    start: date | None, end: date | None, *, label: str = "period"
) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError(f"{label}: end_date < start_date")
