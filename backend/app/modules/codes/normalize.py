"""Alias normalization helpers for code_alias."""

from __future__ import annotations


def normalize_alias(alias: str) -> str:
    """Trim, collapse internal whitespace, and casefold for uniqueness."""
    return " ".join(alias.strip().split()).casefold()


def prepare_aliases(
    aliases: list[str],
    *,
    standard_name: str | None = None,
) -> list[tuple[str, str]]:
    """Return unique (alias, normalized_alias) pairs.

    - Strip empties
    - Dedupe by normalized_alias (first wins)
    - Drop aliases that match the standard name (normalized)
    """
    name_norm = normalize_alias(standard_name) if standard_name else None
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for raw in aliases:
        cleaned = " ".join(raw.strip().split())
        if not cleaned:
            continue
        normalized = normalize_alias(cleaned)
        if not normalized:
            continue
        if name_norm and normalized == name_norm:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((cleaned, normalized))
    return result
