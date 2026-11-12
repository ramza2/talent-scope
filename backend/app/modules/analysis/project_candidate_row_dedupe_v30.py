"""Read-only candidate-to-PDF-row deduplication preview for v30.

Conservative two-tier decisions:
  strict: merge only individually verified SAME group, SAME calendar-month
          dates and literal original-page root evidence (no lossy deletion).
  review: candidate date conflicts with the unique original PDF row and
          a correct sibling exists; report a simulated quarantine only.

Never decides by title alone, page proximity, row ordinal or LLM confidence.
Does not mutate the original candidate or write anything to persistence.
"""
from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate
from app.modules.analysis.compact_v8 import _merge_same_identity_projects
from app.modules.analysis.project_row_match_v30 import folded


def _month(value: object) -> str | None:
    text = str(value or "").strip()
    if text.lower() in ("현재", "진행중", "재직중", "present"):
        return "현재"
    match = re.fullmatch(
        r"((?:19|20)\d{2})[.\-/](0?[1-9]|1[0-2])"
        r"(?:[.\-/](0?[1-9]|[12]\d|3[01]))?",
        text,
    )
    if not match:
        return None
    year, month = int(match[1]), int(match[2])
    if match[3] is not None and int(match[3]) > calendar.monthrange(year, month)[1]:
        return None
    return f"{year:04d}.{month:02d}"


def _unique_groups(groups: list[dict]) -> dict[tuple[str, str], list[dict]]:
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for group in groups:
        if group.get("status") not in {
            "verified_exact_multi_document", "single_source_unmatched"
        }:
            continue
        keys = set()
        for source in group.get("sources") or []:
            name = folded(source.get("project_name"))
            customer = folded(source.get("customer"))
            if name and customer:
                keys.add((name, customer))
        for key in keys:
            index[key].append(group)
    return index


def _root_owned(project: ProjectCandidate, group: dict,
                page_texts: dict[tuple[str, int], str]) -> bool:
    """All root refs must belong to this group's original rows.

    Refs must quote literal *stored page* substrings and identify the project
    title, not a neighboring period or a generic page/header phrase.
    """
    sources = {
        (str(source.get("document_id")), source.get("page_no")):
            folded(source.get("project_name"))
        for source in group.get("sources") or []
    }
    refs = list(project.source_refs or [])
    if not refs:
        return False
    for ref in refs:
        slot = (str(ref.document_id or ""), ref.page_no)
        if slot not in sources:
            return False
        page = page_texts.get(slot) or ""
        quote = (ref.quote_text or "").strip()
        if not page or not quote or quote not in page:
            return False
        quote_fold = folded(quote)
        candidate_name = folded(project.project_name)
        source_name = sources[slot]
        if not (
            candidate_name and candidate_name in quote_fold
            or source_name and source_name in quote_fold
        ):
            return False
    return True


@dataclass
class Preview:
    strict: ProfileCandidateDocument
    quarantine_simulation: ProfileCandidateDocument
    diagnostics: dict[str, Any]


def preview_candidate_row_dedupe(
    candidate: ProfileCandidateDocument,
    *,
    groups: list[dict],
    page_texts: dict[tuple[str, int], str],
) -> Preview:
    index = _unique_groups(groups)
    original = list(candidate.projects)
    exact: dict[str, list[int]] = defaultdict(list)
    conflicts: dict[str, list[int]] = defaultdict(list)
    unmatched = Counter()
    decisions = []

    for i, project in enumerate(original):
        key = (folded(project.project_name), folded(project.customer_name))
        possible = index.get(key, [])
        if len(possible) != 1:
            unmatched["title_customer_not_unique_or_missing"] += 1
            continue
        group = possible[0]
        if not _root_owned(project, group, page_texts):
            unmatched["root_quote_or_source_row_unverified"] += 1
            continue
        start, end = _month(project.start_date), _month(project.end_date)
        expected = group.get("period_months")
        if start is None or end is None or not isinstance(expected, list) or len(expected) != 2:
            unmatched["candidate_or_source_period_unparseable"] += 1
            continue
        if [start, end] == expected:
            exact[str(group["group_id"])].append(i)
        else:
            conflicts[str(group["group_id"])].append(i)

    removals: set[int] = set()
    replacements: dict[int, ProjectCandidate] = {}
    merged_groups = 0
    for gid, indices in exact.items():
        if len(indices) < 2:
            continue
        # All verified against one unique PDF group. Use existing proven
        # relation/ref union, never discard supporting evidence.
        merged = _merge_same_identity_projects([original[i] for i in indices])
        replacements[indices[0]] = merged
        removals.update(indices[1:])
        merged_groups += 1
        decisions.append({
            "kind": "strict_same_group_evidence_merge",
            "group_id": gid,
            "candidate_indices": indices,
            "removed_extra_candidates": len(indices) - 1,
        })

    quarantined: set[int] = set()
    for gid, indices in conflicts.items():
        if not exact.get(gid):
            continue  # no known-good candidate: never remove only candidate
        for idx in indices:
            quarantined.add(idx)
            decisions.append({
                "kind": "date_conflict_review_only",
                "group_id": gid,
                "candidate_index": idx,
                "valid_sibling_indices": exact[gid],
            })

    strict_projects = [
        replacements.get(i, project)
        for i, project in enumerate(original) if i not in removals
    ]
    simulation_projects = [
        replacements.get(i, project)
        for i, project in enumerate(original)
        if i not in removals and i not in quarantined
    ]
    return Preview(
        strict=candidate.model_copy(update={"projects": strict_projects}),
        quarantine_simulation=candidate.model_copy(update={"projects": simulation_projects}),
        diagnostics={
            "original_project_count": len(original),
            "strict_project_count": len(strict_projects),
            "review_quarantine_simulation_count": len(simulation_projects),
            "strict_merged_group_count": merged_groups,
            "strict_removed_extra_count": len(removals),
            "date_conflict_review_count": len(quarantined),
            "exact_source_matched_candidate_count": sum(map(len, exact.values())),
            "unmatched_reason_counts": dict(unmatched),
            "decisions": decisions,
            "caution": (
                "Only strict same-group merges can be considered automatic. "
                "Date-conflict quarantine is a counterfactual review-only "
                "simulation, NOT a recommended deletion or date correction."
            ),
        },
    )
