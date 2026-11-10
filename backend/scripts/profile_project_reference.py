"""Pure, opt-in project reference scorer for the v25/v26 read-only pilot.

The reference is manually reviewed, never inferred from the model output.
Matching is one-to-one by reviewed project-title aliases. Customer and dates
are scored independently so a misplaced row does not appear as a new project.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REFERENCE_SCHEMA = "talentscope-prompt-ab-project-reference-v1"
_DATE = re.compile(r"^(\d{4})[./-](\d{1,2})(?:[./-]\d{1,2})?$")


def _fold(value: str | None) -> str:
    return "".join(ch.casefold() for ch in (value or "") if ch.isalnum())


def _month(value: str | None) -> str:
    raw = (value or "").strip()
    if raw.casefold() in {"현재", "present", "current", "ongoing"}:
        return "현재"
    match = _DATE.fullmatch(raw)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}.{int(match.group(2)):02d}"
    return raw.casefold()


def load_reference(path: Path) -> dict:
    # '-' streams a private reference without a Docker host-file bind mount.
    raw = sys.stdin.read() if path == Path("-") else path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != REFERENCE_SCHEMA:
        raise ValueError("INVALID_PROJECT_REFERENCE_SCHEMA")
    if payload.get("review_status") not in {"provisional", "verified"}:
        raise ValueError("PROJECT_REFERENCE_REVIEW_STATUS_REQUIRED")
    rows = payload.get("projects")
    if not isinstance(rows, list) or not rows:
        raise ValueError("PROJECT_REFERENCE_EMPTY")
    seen_ids = set()
    seen_titles = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            raise ValueError("PROJECT_REFERENCE_INVALID_ROW")
        if row["id"] in seen_ids:
            raise ValueError("PROJECT_REFERENCE_DUPLICATE_ID")
        seen_ids.add(row["id"])
        for field in ("project_name", "customer_name", "start_date", "end_date"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"PROJECT_REFERENCE_MISSING_{field.upper()}")
        for field in ("aliases", "customer_aliases"):
            if not isinstance(row.get(field, []), list) or not all(
                isinstance(v, str) and v.strip() for v in row.get(field, [])
            ):
                raise ValueError(f"PROJECT_REFERENCE_INVALID_{field.upper()}")
        row_titles = set()
        for title in [row["project_name"], *row.get("aliases", [])]:
            folded = _fold(title)
            if not folded or folded in seen_titles:
                raise ValueError("PROJECT_REFERENCE_DUPLICATE_OR_EMPTY_TITLE_ALIAS")
            row_titles.add(folded)
        seen_titles.update(row_titles)
        for field in ("start_date", "end_date"):
            if _month(row[field]) != "현재" and not _DATE.fullmatch(row[field]):
                raise ValueError("PROJECT_REFERENCE_INVALID_DATE")
    return payload


def evaluate_projects(candidate, reference: dict, *, include_names: bool = False) -> dict:
    """Count distinct title coverage and month/customer correctness separately.

    More than one extracted record matching a reviewed title is a duplicate,
    regardless of its customer or period. A single best record per expected
    project is used for field accuracy; extras remain counted as duplicates.
    """
    rows = reference["projects"]
    title_to_index = {
        _fold(title): index
        for index, row in enumerate(rows)
        for title in [row["project_name"], *row.get("aliases", [])]
    }
    groups: dict[int, list] = {i: [] for i in range(len(rows))}
    unknown = []
    for project in candidate.projects:
        index = title_to_index.get(_fold(project.project_name))
        if index is None:
            unknown.append(project)
        else:
            groups[index].append(project)

    matched_titles = 0
    correct_customer = 0
    correct_start = 0
    correct_end = 0
    fully_correct = 0
    duplicates = 0
    missing = []
    repeated = []
    field_issues = []

    for index, row in enumerate(rows):
        found = groups[index]
        if not found:
            missing.append(row)
            continue
        matched_titles += 1
        extras = len(found) - 1
        duplicates += extras
        if extras:
            repeated.append((row, len(found)))

        def matches(project) -> tuple[bool, bool, bool]:
            customers = [row["customer_name"], *row.get("customer_aliases", [])]
            return (
                _fold(project.customer_name) in {_fold(value) for value in customers},
                _month(project.start_date) == _month(row["start_date"]),
                _month(project.end_date) == _month(row["end_date"]),
            )

        best = max(found, key=lambda project: sum(matches(project)))
        customer_ok, start_ok, end_ok = matches(best)
        correct_customer += customer_ok
        correct_start += start_ok
        correct_end += end_ok
        fully_correct += customer_ok and start_ok and end_ok
        if not (customer_ok and start_ok and end_ok):
            field_issues.append((row, best, customer_ok, start_ok, end_ok))

    actual_count = len(candidate.projects)
    summary = {
        "reference_status": reference["review_status"],
        "reference_count": len(rows),
        "actual_count": actual_count,
        "matched_title_count": matched_titles,
        "fully_correct_project_count": fully_correct,
        "matched_customer_count": correct_customer,
        "matched_start_month_count": correct_start,
        "matched_end_month_count": correct_end,
        "missing_title_count": len(missing),
        "unexpected_title_count": len(unknown),
        "duplicate_title_count": duplicates,
        "project_precision_pct": round(100 * fully_correct / actual_count, 2) if actual_count else None,
        "project_recall_pct": round(100 * fully_correct / len(rows), 2),
        "title_recall_pct": round(100 * matched_titles / len(rows), 2),
        "date_granularity": "year_month (not day)",
    }
    if include_names:
        summary["missing_titles"] = [row["project_name"] for row in missing]
        summary["unexpected_titles"] = [project.project_name for project in unknown]
        summary["duplicate_titles"] = [
            {"project_name": row["project_name"], "extracted_count": count}
            for row, count in repeated
        ]
        summary["field_issues"] = [
            {
                "project_name": row["project_name"],
                "wrong_fields": [
                    label for label, ok in (
                        ("customer", customer_ok),
                        ("start_month", start_ok),
                        ("end_month", end_ok),
                    ) if not ok
                ],
                "expected": [row["customer_name"], row["start_date"], row["end_date"]],
                "actual": [best.customer_name, best.start_date, best.end_date],
            }
            for row, best, customer_ok, start_ok, end_ok in field_issues
        ]
    return summary
