"""v30 Source-First PROJECTS *preview* (no DB, models, or profile mutation).

Input: two local private JSON fixtures created by previous read-only pilots.
Exactly one preview row per verified native PDF source group. Different titles
remain different projects, EVEN when same customer/period/role suggests an alias.
All existing LLM candidates are preserved as separately numbered suggestions.
The project title, customer, period and role always come from the source row.

No reference quote is fabricated from a geometric PDF row; relation fields
are PROPOSALS, never silently promoted to confirmed project attributes.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any


AI_FIELDS = (
    "responsibilities", "project_summary", "jobs", "skills",
    "expertise", "business_domains", "customer_types",
)
SOURCE_STATUS = {"verified_exact_multi_document", "single_source_unmatched"}
REASON_VERIFIED = "fully_evidence_bound"


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _ym(value: object, *, allow_ongoing: bool = False) -> str | None:
    """Normalize source months and open-ended *end* dates.

    The native PDF row parser uses '현재' for open-ended projects. Keep that
    established representation instead of rejecting otherwise valid rows.
    Never permit '현재' as a project start date.
    """
    raw = str(value or "").strip()
    if allow_ongoing and raw.casefold() in {"현재", "진행중", "재직중", "present"}:
        return "현재"
    match = re.fullmatch(
        r"((?:19|20)\d{2})[.\-/](0?[1-9]|1[0-2])(?:[.\-/]\d{1,2})?",
        raw,
    )
    return f"{match[1]}.{int(match[2]):02d}" if match else None


def _candidate_enrichment(candidate: dict) -> dict[str, Any]:
    """Preserve original LLM details without interpreting or changing them."""
    result: dict[str, Any] = {}
    for key in AI_FIELDS:
        value = candidate.get(key)
        if key in {"responsibilities", "project_summary"}:
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        elif isinstance(value, list):
            # Original relation refs remain bundled with each code suggestion.
            result[key] = value
    return result


def build_source_first_candidates(
    source_rows_fixture: dict[str, Any],
    ai_fixture: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return redacted public metrics and opt-in private project-only preview."""
    if (
        source_rows_fixture.get("kind") != "talentscope_private_source_first_rows_v30_preview"
        or ai_fixture.get("kind") != "talentscope_readonly_project_candidate_fixture"
        or not isinstance(source_rows_fixture.get("run_id"), str)
        or source_rows_fixture.get("run_id") != ai_fixture.get("run_id")
        or source_rows_fixture.get("no_operational_writes") is not True
    ):
        raise ValueError("SOURCE_FIRST_FIXTURE_MISMATCH")
    groups = source_rows_fixture.get("source_groups")
    candidates = ai_fixture.get("projects")
    unlinked = source_rows_fixture.get("unmatched_candidate_indices")
    pairs = source_rows_fixture.get("review_pairs")
    if (
        not isinstance(groups, list) or not groups
        or not isinstance(candidates, list)
        or not all(isinstance(p, dict) for p in candidates)
        or not isinstance(unlinked, list)
        or not isinstance(pairs, list)
        or not all(isinstance(p, dict) for p in pairs)
    ):
        raise ValueError("MISSING_SOURCE_FIRST_INPUTS")

    ids: set[str] = set()
    used_indices: set[int] = set()
    private_rows: list[dict] = []
    public_states = Counter()
    verified_matches = 0
    suggestions_count = 0
    group_suggestion_counts = []
    for row in groups:
        if not isinstance(row, dict):
            raise ValueError("INVALID_SOURCE_ROW")
        gid = row.get("group_id")
        title = row.get("display_name_from_source")
        customer = row.get("customer_from_source")
        start = row.get("start_month_from_source")
        end = row.get("end_month_from_source")
        source_evidence = row.get("source_row_evidence")
        titles = row.get("original_project_names")
        customers = row.get("original_customers")
        indices = row.get("candidate_indices")
        reasons = row.get("candidate_evidence_reasons")
        if (
            not isinstance(gid, str) or not gid or gid in ids
            or row.get("source_status") not in SOURCE_STATUS
            or row.get("source_of_truth") != "verified_pdf_native_table_row"
            or row.get("safe_to_auto_confirm") is not False
            or not isinstance(title, str) or not title.strip()
            or not isinstance(customer, str) or not customer.strip()
            or not isinstance(start, str) or _ym(start) != start
            or not isinstance(end, str) or _ym(end, allow_ongoing=True) != end
            or not isinstance(titles, list) or title not in titles
            or not isinstance(customers, list) or customer not in customers
            or not isinstance(source_evidence, list) or not source_evidence
            or not isinstance(indices, list) or not isinstance(reasons, list)
            or len(indices) != len(reasons)
        ):
            raise ValueError("SOURCE_ROW_SCHEMA_INVALID")
        ids.add(gid)
        for e in source_evidence:
            bbox = e.get("bbox") if isinstance(e, dict) else None
            if (
                not isinstance(e, dict)
                or not isinstance(e.get("row_id"), str) or not e["row_id"]
                or not isinstance(e.get("document_id"), str) or not e["document_id"]
                or not isinstance(e.get("page_no"), int) or e["page_no"] <= 0
                or not isinstance(bbox, list) or len(bbox) != 4
                or not all(type(v) in (float, int) for v in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]
            ):
                raise ValueError("SOURCE_ROW_EVIDENCE_MISSING")
        if any(
            not isinstance(i, int) or isinstance(i, bool) or i < 0
            or i >= len(candidates) or i in used_indices
            for i in indices
        ) or len(set(indices)) != len(indices):
            raise ValueError("AI_CANDIDATE_LINKS_INVALID")
        used_indices.update(indices)

        suggestions = []
        preferred_indices = []
        for i, reason in zip(indices, reasons):
            if not isinstance(reason, str):
                raise ValueError("AI_EVIDENCE_REASON_INVALID")
            cand = candidates[i]
            identity_ok = (
                _fold(cand.get("project_name")) in {_fold(t) for t in titles}
                and _fold(cand.get("customer_name")) in {_fold(c) for c in customers}
            )
            month_ok = _ym(cand.get("start_date")) == start and _ym(cand.get("end_date"), allow_ongoing=True) == end
            fully_verified = reason == REASON_VERIFIED and identity_ok and month_ok
            if fully_verified:
                verified_matches += 1
                preferred_indices.append(i)
            suggestions.append({
                "candidate_index": i,
                "evidence_reason": reason,
                "candidate_title_customer_match": identity_ok,
                "candidate_period_matches_original_months": month_ok,
                "candidate_root_evidence_verified": fully_verified,
                # Even verified root evidence does not validate EACH proposed
                # code, summary or responsibility, so keep proposals separate.
                "proposed_enrichment_not_automatically_accepted": _candidate_enrichment(cand),
            })
        suggestions_count += len(suggestions)
        # Prefer only ONE well-evidenced candidate; other candidates remain
        # independently reviewable and never disappear.
        preferred = preferred_indices[0] if len(preferred_indices) == 1 else None
        state = (
            "source_only" if not indices
            else "one_ai_candidate" if len(indices) == 1
            else "multiple_ai_candidates"
        )
        public_states[state] += 1
        group_suggestion_counts.append({
            "group_id": gid,
            "source_status": row["source_status"],
            "ai_candidate_indices": list(indices),
            "verified_root_candidate_indices": preferred_indices,
            "preferred_enrichment_candidate_index": preferred,
            "name_variant_for_post_registration_optional_review": bool(
                row.get("review_name_variant_pair")
            ),
        })
        private_rows.append({
            "group_id": gid,
            "source_status": row["source_status"],
            # Only these SOURCE fields form an independent project identity.
            "source_project": {
                "project_name": title,
                "customer_name": customer,
                "start_date": start,
                "end_date": end,
                "original_titles": list(titles),
                "original_customers": list(customers),
                "roles_from_pdf_row": list(row.get("roles_from_source") or []),
            },
            "native_row_provenance": source_evidence,
            "enrichment_suggestions": suggestions,
            "preferred_enrichment_candidate_index": preferred,
            "name_variant_for_post_registration_optional_review": bool(
                row.get("review_name_variant_pair")
            ),
            "requires_pre_registration_name_merge": False,
            "suitable_for_user_bulk_review_preview": True,
            "automatic_confirmation": False,
            "source_refs_not_fabricated": True,
        })

    if (
        any(type(i) is not int or i < 0 or i >= len(candidates) for i in unlinked)
        or len(unlinked) != len(set(unlinked))
        or used_indices & set(unlinked)
        or used_indices | set(unlinked) != set(range(len(candidates)))
    ):
        raise ValueError("AI_CANDIDATE_COVERAGE_INVALID")
    paired: set[str] = set()
    for pair in pairs:
        a, b = pair.get("group_ids", [None, None]) if isinstance(pair.get("group_ids"), list) and len(pair["group_ids"]) == 2 else (None, None)
        if (
            pair.get("decision") != "manual_review_only_no_auto_merge"
            or not a or not b or a == b or a not in ids or b not in ids
            or a in paired or b in paired
        ):
            raise ValueError("UNREVIEWED_NAME_VARIANT_PAIR_INVALID")
        paired.update((a, b))
    flagged = {r["group_id"] for r in private_rows
               if r["name_variant_for_post_registration_optional_review"]}
    if flagged != paired:
        raise ValueError("NAME_VARIANT_REVIEW_FLAGS_MISMATCH")
    if len(private_rows) != len(groups) or len({r["group_id"] for r in private_rows}) != len(groups):
        raise ValueError("SOURCE_GROUP_LOST")
    private = {
        "kind": "talentscope_private_source_first_candidate_preview_v30",
        "run_id": source_rows_fixture["run_id"],
        "source_sha256": ai_fixture.get("source_sha256"),
        "prior_candidate_sha256": ai_fixture.get("existing_candidate_sha256"),
        "project_count": len(private_rows),
        "projects": private_rows,
        "unlinked_ai_candidate_indices": unlinked,
        "name_variant_pairs_remain_independent": [
            pair["group_ids"] for pair in pairs
        ],
        "mode": "read_only_review_preview_not_operational_candidate",
    }
    public = {
        "mode": "source_first_v30_project_preview_no_ai_no_db",
        "run_id": source_rows_fixture["run_id"],
        "native_project_group_count": len(groups),
        "proposed_project_count": len(private_rows),
        "source_only_project_count": public_states["source_only"],
        "one_ai_candidate_group_count": public_states["one_ai_candidate"],
        "multiple_ai_candidate_group_count": public_states["multiple_ai_candidates"],
        "candidate_enrichment_suggestion_count": suggestions_count,
        "verified_root_candidate_count": verified_matches,
        "unlinked_ai_candidate_count": len(unlinked),
        "name_variant_pair_count": len(pairs),
        "name_variant_groups_kept_separate": len(paired),
        "no_pre_registration_name_merge": True,
        "bulk_review_preview_enabled": True,
        "automatically_confirmed": 0,
        "no_model_calls": True,
        "no_operational_db_or_candidate_writes": True,
        "groups_index_only": group_suggestion_counts,
        "note": (
            "This is a PREVIEW, not ProfileCandidateDocument or confirmed project. "
            "Original PDF project names, customer, months and roles are authoritative. "
            "All AI details remain suggestions and source_refs are NOT fabricated. "
            "Different titles remain separate; users may edit/merge after confirmation."
        ),
    }
    return public, private
