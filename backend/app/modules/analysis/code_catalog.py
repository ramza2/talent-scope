"""Reusable code-catalog text formatting for profile extraction prompts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol


class _CatalogRow(Protocol):
    code: str
    code_type: str
    name: str


CORE_CATALOG_TYPES: tuple[str, ...] = ("JOB", "TECH", "EXP")
PROJECTS_CATALOG_TYPES: tuple[str, ...] = (
    "JOB",
    "TECH",
    "EXP",
    "BIZ",
    "CUSTOMER_TYPE",
)


def format_code_catalog(
    codes: Sequence[Any] | Iterable[Any],
    aliases: dict[str, list[str]] | None = None,
    *,
    max_chars: int,
    code_types: Sequence[str] | frozenset[str] | None = None,
) -> str:
    """Format catalog lines, optionally restricted to ``code_types``.

    Rows must expose ``code``, ``code_type``, and ``name`` attributes.
    Type order follows first appearance in ``codes`` (caller should pre-sort).
    """
    if max_chars <= 0:
        return ""
    allowed = frozenset(code_types) if code_types is not None else None
    alias_map = aliases or {}
    lines: list[str] = []
    used = 0
    current_type: str | None = None
    for row in codes:
        code_type = str(getattr(row, "code_type", "") or "")
        if allowed is not None and code_type not in allowed:
            continue
        if code_type != current_type:
            current_type = code_type
            header = f"\n[{current_type}]\n"
            if used + len(header) > max_chars:
                break
            lines.append(header)
            used += len(header)
        code = str(getattr(row, "code", "") or "")
        name = str(getattr(row, "name", "") or "")
        alias_part = "|".join(alias_map.get(code, []))
        if alias_part:
            line = f"{code}\t{name}\t{alias_part}\n"
        else:
            line = f"{code}\t{name}\n"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "".join(lines).strip()
