"""Conservative PROJECT recall from validated DOC-KOSA seven-column tables.

This only supplements AI Candidates. It never updates a confirmed profile and
does not replace or silently correct conflicting LLM-extracted project values.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Mapping

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)


_PERIOD_RE = re.compile(
    r"^\s*((?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?)"
    r"\s*[~～]\s*"
    r"((?:19|20)\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?)\s*$"
)
_DATE_RE = re.compile(r"^((?:19|20)\d{2})[./-](\d{1,2})(?:[./-](\d{1,2}))?$")
_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣]+")


def _date_parts(value: str) -> tuple[int, int, int] | None:
    match = _DATE_RE.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day = (int(match[1]), int(match[2]), int(match[3] or 1))
    try:
        date(year, month, day)
    except ValueError:
        return None
    return year, month, day


def _match_key(value: str | None) -> str:
    return _NON_WORD_RE.sub("", unicodedata.normalize("NFKC", value or "").lower())


def _project_title_key(value: str | None) -> str:
    """Treat only a nontrivial final '프로젝트' label as an optional alias."""
    key = _match_key(value)
    suffix = "프로젝트"
    if key.endswith(suffix) and len(key) - len(suffix) >= 4:
        return key[: -len(suffix)]
    return key


def _month(value: str | None) -> tuple[int, int] | None:
    parts = _date_parts(value or "")
    return parts[:2] if parts else None


def _validated_rows(text: str) -> list[tuple[str, str, str, str, str]]:
    """All-or-nothing table parsing; partial/truncated OCR is never promoted."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not 2 <= len(lines) <= 48:
        return []
    rows: list[tuple[str, str, str, str, str]] = []
    for line in lines:
        # Root quote_text must be the exact complete source row (<= 500 chars).
        if len(line) > 500 or line.count("|") not in (6, 7, 8):
            return []
        cells = [value.strip() for value in line.strip("|").split("|")]
        if len(cells) != 7 or not cells[1] or not cells[3]:
            return []
        period = _PERIOD_RE.fullmatch(cells[2])
        if period is None:
            return []
        start, end = period.group(1, 2)
        start_parts, end_parts = _date_parts(start), _date_parts(end)
        if start_parts is None or end_parts is None or start_parts > end_parts:
            return []
        rows.append((cells[1], cells[3], start, end, line))
    return rows


def supplement_kosa_table_projects(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: Mapping[tuple[str, int], str] | None,
    document_type_by_id: Mapping[str, str],
) -> tuple[ProfileCandidateDocument, dict[str, int]]:
    """Keep every validated KOSA row evidenced in the *reviewable* candidate.

    Exact customer and period-month agreement is required; titles may only differ
    by a final generic '프로젝트' suffix. Other ambiguities stay reviewable.
    """
    metrics = {"table_rows": 0, "matched_rows": 0, "supplemented_rows": 0}
    if not page_texts:
        return candidate, metrics

    # A copy also prevents accidental changes to previously extracted candidates.
    projects = [project.model_copy(deep=True) for project in candidate.projects]
    for (document_id, page_no), text in sorted(page_texts.items()):
        if document_type_by_id.get(document_id) != "DOC-KOSA":
            continue
        rows = _validated_rows(text)
        for title, customer, start, end, quote in rows:
            metrics["table_rows"] += 1
            ref = SourceRef(document_id=document_id, page_no=page_no, quote_text=quote)
            matching = next(
                (
                    project
                    for project in projects
                    if _project_title_key(project.project_name) == _project_title_key(title)
                    and _match_key(project.customer_name) == _match_key(customer)
                    and _month(project.start_date) == _month(start)
                    and _month(project.end_date) == _month(end)
                ),
                None,
            )
            if matching is not None:
                if ref not in matching.source_refs:
                    matching.source_refs.append(ref)
                metrics["matched_rows"] += 1
                continue

            projects.append(
                ProjectCandidate(
                    project_name=title,
                    customer_name=customer,
                    start_date=start,
                    end_date=end,
                    source_refs=[ref],
                )
            )
            metrics["supplemented_rows"] += 1

    if not metrics["supplemented_rows"] and not metrics["matched_rows"]:
        return candidate, metrics
    return candidate.model_copy(update={"projects": projects}), metrics
