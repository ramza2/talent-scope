"""Opt-in v30 read-only dedup preview with geometrically owned row-field anchors.

This is a *second* conservative tier, separate from existing strict
title-bearing root quotes. A quote lacking the whole title is acceptable
only when the ORIGINAL literal quote uniquely matches a project title
fragment or complete date-period cell of one native PDF row on its page.

Never merge by name alone, date alone, role/customer quote, page proximity,
fuzzy title, or source-reference page without row-specific content.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate
from app.modules.analysis.compact_v8 import _merge_same_identity_projects
from app.modules.analysis.project_candidate_row_dedupe_v30 import (
    _month, _root_owned, _unique_groups,
)
from app.modules.analysis.project_row_match_v30 import folded


@dataclass
class RowCellPreview:
    candidate: ProfileCandidateDocument
    stats: dict[str, Any]


def _native_rows_on_page(groups: list[dict]) -> dict[tuple[str, int], list[dict]]:
    index: dict[tuple[str, int], list[dict]] = defaultdict(list)
    seen: set[tuple[str, int, str]] = set()
    for group in groups:
        if group.get("status") not in {
            "verified_exact_multi_document", "single_source_unmatched"
        }:
            continue
        for source in group.get("sources") or []:
            slot = (str(source.get("document_id")), source.get("page_no"))
            rid = source.get("row_id")
            if not isinstance(slot[1], int) or not rid:
                continue
            unique = (slot[0], slot[1], str(rid))
            if unique in seen:
                continue
            seen.add(unique)
            index[slot].append(source)
    return index


def _row_specific_anchor(quote: str, source: dict) -> bool:
    """Only a sufficiently long title fragment or full period-cell quote.

    Short month tokens, customer names and generic role quotes never qualify.
    """
    token = folded(quote)
    title = folded(source.get("project_name"))
    period = folded(source.get("period_raw"))
    title_anchor = (
        len(token) >= 8 and sum(ch.isalpha() for ch in token) >= 5
        and token in title
    )
    # Full native period-cell substring, not an isolated start/end month.
    # At least 8 numeric digits (e.g. two years). A longer string containing
    # headers, role/customer or neighboring rows cannot pass.
    period_anchor = (
        len(token) >= 10
        and sum(ch.isdigit() for ch in token) >= 8
        and token in period
    )
    return title_anchor or period_anchor


def _unique_anchored_root_refs(
    project: ProjectCandidate, group: dict,
    *,
    groups_by_page: dict[tuple[str, int], list[dict]],
    page_texts: dict[tuple[str, int], str],
) -> bool:
    sources = {
        (str(s.get("document_id")), s.get("page_no"), str(s.get("row_id"))): s
        for s in group.get("sources") or []
    }
    refs = list(project.source_refs or [])
    if not refs:
        return False
    for ref in refs:
        slot = (str(ref.document_id or ""), ref.page_no)
        quote = (ref.quote_text or "").strip()
        if not quote or quote not in (page_texts.get(slot) or ""):
            return False
        own = [
            s for (doc, page, _rid), s in sources.items()
            if (doc, page) == slot and _row_specific_anchor(quote, s)
        ]
        if len(own) != 1:
            return False
        # The same quote may not anchor any other native row on this page.
        own_rid = str(own[0].get("row_id"))
        for other in groups_by_page.get(slot, []):
            if str(other.get("row_id")) != own_rid and _row_specific_anchor(quote, other):
                return False
    return True


def preview_row_cell_merge(
    candidate: ProfileCandidateDocument,
    *,
    groups: list[dict],
    page_texts: dict[tuple[str, int], str],
) -> RowCellPreview:
    name_index = _unique_groups(groups)
    page_index = _native_rows_on_page(groups)
    originals = list(candidate.projects)
    eligible: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    reasons = Counter()
    evidence = {}
    for idx, project in enumerate(originals):
        group_matches = name_index.get(
            (folded(project.project_name), folded(project.customer_name)), []
        )
        if len(group_matches) != 1:
            reasons["no_unique_title_customer_group"] += 1
            continue
        group = group_matches[0]
        period = group.get("period_months")
        if (
            not isinstance(period, list) or len(period) != 2
            or [_month(project.start_date), _month(project.end_date)] != period
        ):
            reasons["period_not_exact_to_source_months"] += 1
            continue
        if _root_owned(project, group, page_texts):
            anchor = "literal_full_title"
        elif _unique_anchored_root_refs(
            project, group, groups_by_page=page_index, page_texts=page_texts
        ):
            anchor = "unique_native_row_title_or_period_cell"
        else:
            reasons["root_quote_not_uniquely_row_anchored"] += 1
            continue
        evidence[idx] = anchor
        # NEVER merge different explicitly stated day-level periods.
        key = (
            str(group["group_id"]),
            (project.start_date or "").strip(),
            (project.end_date or "").strip(),
        )
        eligible[key].append(idx)
    merged = {}
    consumed = set()
    decisions = []
    for (gid, _start, _end), indices in eligible.items():
        if len(indices) < 2:
            continue
        merged[indices[0]] = _merge_same_identity_projects(
            [originals[idx] for idx in indices]
        )
        consumed.update(indices[1:])
        decisions.append({
            "group_id": gid,
            "candidate_indices": indices,
            "anchor_kinds": [evidence[i] for i in indices],
            "merged_extra_count": len(indices)-1,
        })
    output = [
        merged.get(idx, project)
        for idx, project in enumerate(originals) if idx not in consumed
    ]
    return RowCellPreview(
        candidate=candidate.model_copy(update={"projects": output}),
        stats={
            "raw_count": len(originals),
            "preview_count": len(output),
            "merged_extra_count": len(consumed),
            "merged_group_count": len(decisions),
            "eligible_candidate_count": len(evidence),
            "eligible_evidence_counts": dict(Counter(evidence.values())),
            "reject_counts": dict(reasons),
            "decisions": decisions,
            "policy": (
                "Read-only preview. Native row cells and unique literal source "
                "quotes are required for newly eligible candidates. No fuzzy "
                "matches, no day-level period collisions and no data writes."
            ),
        },
    )
