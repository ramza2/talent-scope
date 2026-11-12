#!/usr/bin/env python3
"""No-AI / no-DB, index-only source-first project identity preview.

Inputs are the existing v30 read-only cluster JSON, redacted dedup diagnostics,
and redacted row-cell evaluation. This script neither loads private candidate
fixtures nor changes profile/candidate records. All source group IDs remain
distinct: three potential alias pairs are ONLY human-review suggestions.

Only group IDs, candidate indices, reason codes and counts are printed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CLUSTER_PASS = "TALENTSCOPE_V30_ROW_CLUSTERS_READONLY_PASS"
DIAGNOSTIC_PASS = "TALENTSCOPE_V30_DEDUPE_REASON_READONLY_PASS"
ROWCELL_PASS = "TALENTSCOPE_V30_ROW_CELL_DEDUPE_READONLY_PASS"
PASS = "TALENTSCOPE_V30_SOURCE_FIRST_PREVIEW_PASS"
ALLOWED_STATUS = {"verified_exact_multi_document", "single_source_unmatched"}


def read_report(path: Path, marker: str) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.rstrip().endswith(marker):
        raise ValueError(f"MISSING_PASS_MARKER:{path.name}")
    result = json.loads(raw.split(marker, 1)[0])
    if not isinstance(result, dict):
        raise ValueError("INVALID_REPORT_OBJECT")
    return result


def build_preview(
    clusters: dict[str, Any],
    diagnosis: dict[str, Any],
    rowcell: dict[str, Any],
) -> dict[str, Any]:
    if (
        not clusters.get("run_id")
        or clusters.get("run_id") != diagnosis.get("run_id")
        or diagnosis.get("run_id") != rowcell.get("run_id")
        or clusters.get("candidate_sha256_unchanged")
        != diagnosis.get("candidate_sha256_unchanged")
        or diagnosis.get("candidate_sha256_unchanged")
        != rowcell.get("candidate_sha256_unchanged")
    ):
        raise ValueError("SOURCE_OR_RUN_FINGERPRINT_MISMATCH")
    if (
        clusters.get("mode") != "readonly_v30_row_cluster_preview"
        or diagnosis.get("diagnostics", {}).get("mode")
        != "readonly_reason_diagnostics_no_ai_no_mutation"
        or rowcell.get("mode") != "readonly_no_model_calls"
    ):
        raise ValueError("INVALID_READONLY_REPORT_MODES")

    groups = clusters.get("groups")
    if not isinstance(groups, list) or len(groups) != clusters.get("group_count"):
        raise ValueError("CLUSTERS_MISSING_OR_COUNT_MISMATCH")
    ids = [g.get("group_id") for g in groups]
    if (
        not all(isinstance(x, str) and x for x in ids)
        or len(set(ids)) != len(ids)
        or any(g.get("status") not in ALLOWED_STATUS for g in groups)
    ):
        raise ValueError("UNVERIFIED_OR_DUPLICATE_SOURCE_GROUPS")
    groups_by_id = {g["group_id"]: g for g in groups}

    if sum(g["status"] == "verified_exact_multi_document" for g in groups) != clusters.get(
        "safe_multi_document_group_count"
    ):
        raise ValueError("SOURCE_EXACT_GROUP_COUNT_MISMATCH")

    details = diagnosis["diagnostics"]
    entries = details.get("per_candidate")
    if not isinstance(entries, list) or len(entries) != details.get("total_candidate_count"):
        raise ValueError("CANDIDATE_DETAIL_COUNT_MISMATCH")
    indices = [entry.get("index") for entry in entries]
    if set(indices) != set(range(len(entries))) or len(set(indices)) != len(entries):
        raise ValueError("CANDIDATE_INDEX_INCOMPLETE")

    attachments: dict[str, list[dict]] = defaultdict(list)
    unmatched: list[dict] = []
    for entry in entries:
        # Do not infer an identity from a reference title or from row order.
        gid = entry.get("group_id")
        sample = {"index": entry["index"], "reason": entry["reason"]}
        if gid is None:
            unmatched.append(sample)
        elif gid in groups_by_id:
            attachments[gid].append(sample)
        else:
            raise ValueError("CANDIDATE_LINKS_TO_UNKNOWN_SOURCE_GROUP")

    suggestions = clusters.get("review_suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("MISSING_REVIEW_SUGGESTIONS")
    review_pairs: list[dict] = []
    seen_pair_groups: set[str] = set()
    for suggestion in suggestions:
        pair = suggestion.get("group_ids")
        if (
            suggestion.get("status") != "needs_review_no_auto_merge"
            or not isinstance(pair, list)
            or len(pair) != 2
            or pair[0] == pair[1]
            or any(g not in groups_by_id or groups_by_id[g]["status"] != "single_source_unmatched" for g in pair)
            or any(g in seen_pair_groups for g in pair)
        ):
            raise ValueError("INVALID_REVIEW_ONLY_SOURCE_PAIR")
        seen_pair_groups.update(pair)
        review_pairs.append({
            "group_ids": pair,
            "candidate_indices": [
                [item["index"] for item in attachments.get(gid, [])]
                for gid in pair
            ],
            "decision": "manual_review_only_no_auto_merge",
        })

    if len(entries) != rowcell.get("project_count"):
        raise ValueError("ROWCELL_CANDIDATE_COUNT_MISMATCH")
    raw_eval = (rowcell.get("evaluations") or {}).get("raw") or {}
    if (
        raw_eval.get("actual_count") != len(entries)
        or raw_eval.get("duplicate_title_count") != details.get("reference_duplicate_extras")
    ):
        raise ValueError("RAW_REFERENCE_METRIC_MISMATCH")
    sorted_groups = sorted(groups, key=lambda g: g["group_id"])
    covered = sum(bool(attachments.get(g["group_id"])) for g in groups)
    multiple = sum(len(attachments.get(g["group_id"], [])) > 1 for g in groups)
    extras = sum(max(0, len(attachments.get(g["group_id"], [])) - 1) for g in groups)
    return {
        "mode": "source_first_identity_preview_no_ai_no_db",
        "run_id": clusters["run_id"],
        "read_only": True,
        "no_candidate_text_in_output": True,
        "source_group_count": len(groups),
        "source_exact_multi_document_group_count": clusters["safe_multi_document_group_count"],
        "source_single_document_group_count": len(groups) - clusters["safe_multi_document_group_count"],
        "source_groups_with_candidate": covered,
        "source_groups_without_candidate": len(groups) - covered,
        "source_groups_with_multiple_candidates": multiple,
        "candidate_count": len(entries),
        "candidates_attached_by_title_customer": len(entries) - len(unmatched),
        "unmatched_candidate_count": len(unmatched),
        "duplicate_candidate_attachments_to_same_source_group": extras,
        "provisional_reference_duplicate_extras": raw_eval["duplicate_title_count"],
        "candidate_unmatched": unmatched,
        "review_only_pair_count": len(review_pairs),
        "review_only_pairs": review_pairs,
        "group_previews": [
            {
                "group_id": group["group_id"],
                "status": group["status"],
                "candidate_indices": [e["index"] for e in attachments.get(group["group_id"], [])],
                "candidate_evidence_reasons": [
                    e["reason"] for e in attachments.get(group["group_id"], [])
                ],
                "recommendation": (
                    "source_row_without_llm_candidate"
                    if not attachments.get(group["group_id"])
                    else "multi_candidate_enrichment_review"
                    if len(attachments[group["group_id"]]) > 1
                    else "one_candidate_enrichment"
                ),
            }
            for group in sorted_groups
        ],
        "meaning": (
            "These are source-row identities with tentative title+customer AI links, "
            "NOT verified AI evidence ownership. No automatic merge, deletion, "
            "date change, or promotion to production. The review-only pairs "
            "remain separate pending human confirmation."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--diagnostics-file", required=True, type=Path)
    parser.add_argument("--rowcell-report", required=True, type=Path)
    args = parser.parse_args()
    result = build_preview(
        read_report(args.clusters_file, CLUSTER_PASS),
        read_report(args.diagnostics_file, DIAGNOSTIC_PASS),
        read_report(args.rowcell_report, ROWCELL_PASS),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
