"""Helpers for translating DB IntegrityError into application errors."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def is_unique_violation(exc: IntegrityError, *constraint_markers: str) -> bool:
    """Return True when ``exc`` is a PostgreSQL unique violation for a known constraint.

    Markers are matched against the constraint name and the error text so we do
    not map unrelated IntegrityError cases to duplicate Application Errors.
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None and str(sqlstate) != "23505":
        return False

    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    haystack = " ".join(
        part
        for part in (
            constraint,
            str(orig) if orig is not None else None,
            str(exc),
        )
        if part
    ).lower()

    if sqlstate is None and "unique" not in haystack and "duplicate" not in haystack:
        return False

    if not constraint_markers:
        return str(sqlstate) == "23505" if sqlstate is not None else False

    return any(marker.lower() in haystack for marker in constraint_markers)
