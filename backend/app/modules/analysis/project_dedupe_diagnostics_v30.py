"""Read-only reasons why v30 candidate rows cannot be safely deduplicated.

Count-only and index-only output. NEVER include names, quotes, private page
text, full candidates, or inferred associations in the returned report.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate
from app.modules.analysis.project_candidate_row_dedupe_v30 import _month, _unique_groups
from app.modules.analysis.project_row_match_v30 import folded
from scripts.profile_project_reference import _fold


def _classification(
    project: ProjectCandidate,
    *,
    index: dict[tuple[str, str], list[dict]],
    page_texts: dict[tuple[str, int], str],
) -> tuple[str, str | None]:
    key = (folded(project.project_name), folded(project.customer_name))
    options = index.get(key, [])
    if not options:
        return "title_customer_no_group", None
    if len(options) != 1:
        return "title_customer_ambiguous_group", None
    group = options[0]
    gid = str(group["group_id"])
    owned = {
        (str(s.get("document_id")), s.get("page_no")): folded(s.get("project_name"))
        for s in group.get("sources", [])
    }
    refs = list(project.source_refs or [])
    if not refs:
        return "root_refs_missing", gid
    for ref in refs:
        slot = (str(ref.document_id or ""), ref.page_no)
        if slot not in owned:
            return "ref_page_not_in_group", gid
        page = page_texts.get(slot) or ""
        if not page:
            return "stored_page_missing", gid
        quote = (ref.quote_text or "").strip()
        if not quote:
            return "root_quote_missing", gid
        if quote not in page:
            return "quote_not_literal_on_stored_page", gid
        quote_name = folded(quote)
        if not (
            folded(project.project_name) in quote_name
            or owned[slot] in quote_name
        ):
            return "literal_quote_lacks_project_title", gid
    start, end = _month(project.start_date), _month(project.end_date)
    expected = group.get("period_months")
    if not start or not end or not isinstance(expected, list) or len(expected) != 2:
        return "candidate_or_group_period_unparseable", gid
    if [start, end] != expected:
        return "source_period_conflict", gid
    return "fully_evidence_bound", gid


def diagnose(
    candidate: ProfileCandidateDocument,
    *,
    groups: list[dict],
    page_texts: dict[tuple[str, int], str],
    reference: dict,
) -> dict[str, Any]:
    index = _unique_groups(groups)
    reasons: Counter[str] = Counter()
    by_index: list[dict] = []
    for i, project in enumerate(candidate.projects):
        reason, group_id = _classification(project, index=index, page_texts=page_texts)
        reasons[reason] += 1
        by_index.append({"index": i, "reason": reason, "group_id": group_id})

    title_to_id: dict[str, int] = {
        _fold(title): row["id"]
        for row in reference["projects"]
        for title in [row["project_name"], *row.get("aliases", [])]
    }
    ref_to_candidates: dict[int, list[int]] = defaultdict(list)
    outside: list[int] = []
    for idx, p in enumerate(candidate.projects):
        ref_id = title_to_id.get(_fold(p.project_name))
        if ref_id is None:
            outside.append(idx)
        else:
            ref_to_candidates[ref_id].append(idx)
    duplicate_entries = []
    for reference_id, indices in sorted(ref_to_candidates.items()):
        if len(indices) <= 1:
            continue
        duplicate_entries.append({
            "reference_id": reference_id,
            "candidate_indices": indices,
            "reason_by_candidate": [
                {"index": i, "reason": by_index[i]["reason"], "group_id": by_index[i]["group_id"]}
                for i in indices
            ],
        })
    return {
        "mode": "readonly_reason_diagnostics_no_ai_no_mutation",
        "total_candidate_count": len(candidate.projects),
        "reason_counts": dict(sorted(reasons.items())),
        "per_candidate": by_index,
        "reference_duplicate_extras": sum(len(x["candidate_indices"]) - 1 for x in duplicate_entries),
        "reference_duplicate_groups": duplicate_entries,
        "out_of_reference_candidate_indices": outside,
        "note": (
            "Reasons are first failing guard in conservative order, not proof "
            "of whether a project exists. Never delete by these classifications."
        ),
    }
