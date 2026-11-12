#!/usr/bin/env python3
"""Source-first project-list preview from native PDF rows, without model or DB.

Consumes the verified native-row clusters and index-only candidate coverage
report from the SAME run. Preserves all source rows, including those without
LLM candidates. Does NOT merge reviewed-name variants, trust unverified LLM
fields, or create/modify candidate, profile, diff, or database records.

stdout: redacted counts and group-ID/index-only preview.
--private-rows-output: opt-in 0600 source-field JSON for local-only inspection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.profile_project_source_first_preview_noai import (
    CLUSTER_PASS, PASS as SOURCE_PASS, read_report,
)

PASS = "TALENTSCOPE_V30_SOURCE_ROW_LIST_READONLY_PASS"


def _valid_source(source: dict) -> bool:
    bbox = source.get("bbox")
    return (
        bool(source.get("row_id"))
        and isinstance(source.get("document_id"), str)
        and bool(source.get("document_id"))
        and isinstance(source.get("page_no"), int)
        and source["page_no"] > 0
        and isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(x, (float, int)) and not isinstance(x, bool) for x in bbox)
        and bbox[2] > bbox[0]
        and bbox[3] > bbox[1]
        and isinstance(source.get("project_name"), str)
        and bool(source["project_name"].strip())
        and isinstance(source.get("customer"), str)
        and bool(source["customer"].strip())
    )


def build_rows(
    clusters: dict[str, Any],
    source_preview: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        clusters.get("mode") != "readonly_v30_row_cluster_preview"
        or source_preview.get("mode") != "source_first_identity_preview_no_ai_no_db"
        or clusters.get("run_id") != source_preview.get("run_id")
        or not source_preview.get("read_only")
        or not source_preview.get("no_candidate_text_in_output")
    ):
        raise ValueError("INVALID_SOURCE_FIRST_INPUT")

    groups = clusters.get("groups")
    pre = source_preview.get("group_previews")
    if not isinstance(groups, list) or len(groups) != clusters.get("group_count"):
        raise ValueError("SOURCE_GROUP_COUNT_MISMATCH")
    if not isinstance(pre, list) or len(pre) != len(groups):
        raise ValueError("COVERAGE_GROUP_COUNT_MISMATCH")
    g_by_id = {g.get("group_id"): g for g in groups}
    p_by_id = {p.get("group_id"): p for p in pre}
    if len(g_by_id) != len(groups) or len(p_by_id) != len(pre) or set(g_by_id) != set(p_by_id):
        raise ValueError("SOURCE_GROUP_ID_MISMATCH")
    pairs = source_preview.get("review_only_pairs")
    if not isinstance(pairs, list) or len(pairs) != source_preview.get("review_only_pair_count"):
        raise ValueError("REVIEW_PAIR_MISMATCH")
    flagged: set[str] = set()
    for pair in pairs:
        ids = pair.get("group_ids") or []
        if (
            pair.get("decision") != "manual_review_only_no_auto_merge"
            or len(ids) != 2 or len(set(ids)) != 2
            or any(g not in g_by_id or g in flagged or
                   g_by_id[g].get("status") != "single_source_unmatched" for g in ids)
        ):
            raise ValueError("INVALID_MANUAL_REVIEW_PAIR")
        flagged.update(ids)

    source_rows = []
    redacted_groups = []
    attached_indices: set[int] = set()
    state_counts = Counter()
    for gid in sorted(g_by_id):
        group, item = g_by_id[gid], p_by_id[gid]
        status = group.get("status")
        sources = group.get("sources")
        periods = group.get("period_months")
        if (
            status not in {"verified_exact_multi_document", "single_source_unmatched"}
            or item.get("status") != status
            or not isinstance(sources, list) or not sources
            or not all(isinstance(s, dict) and _valid_source(s) for s in sources)
            or not isinstance(periods, list) or len(periods) != 2
            or not all(isinstance(p, str) and p for p in periods)
        ):
            raise ValueError("SOURCE_ROW_PROVENANCE_INVALID")
        doc_ids = [s["document_id"] for s in sources]
        if status == "verified_exact_multi_document":
            if len(sources) < 2 or len(doc_ids) != len(set(doc_ids)):
                raise ValueError("SOURCE_MULTI_DOCUMENT_IDENTITY_INVALID")
        elif len(sources) != 1:
            raise ValueError("SOURCE_SINGLE_DOCUMENT_IDENTITY_INVALID")

        candidates = item.get("candidate_indices")
        reasons = item.get("candidate_evidence_reasons")
        if (
            not isinstance(candidates, list) or not isinstance(reasons, list)
            or len(candidates) != len(reasons)
            or any(not isinstance(i, int) or isinstance(i, bool)
                   or i < 0 or i in attached_indices for i in candidates)
            or any(not isinstance(reason, str) for reason in reasons)
        ):
            raise ValueError("CANDIDATE_INDEX_LINKAGE_INVALID")
        attached_indices.update(candidates)

        if not candidates:
            state = "source_row_without_candidate"
        elif len(candidates) > 1:
            state = "multi_candidate_review"
        elif reasons[0] == "fully_evidence_bound":
            state = "one_verified_candidate"
        else:
            state = "one_candidate_evidence_review"
        state_counts[state] += 1
        raw_titles = sorted(set(s["project_name"] for s in sources))
        raw_customers = sorted(set(s["customer"] for s in sources))
        raw_roles = sorted(set(
            str(s.get("role") or "").strip() for s in sources if str(s.get("role") or "").strip()
        ))
        canonical_source_title = sorted(raw_titles, key=lambda s: (-len(s), s))[0]
        row = {
            "group_id": gid,
            "source_status": status,
            "display_name_from_source": canonical_source_title,
            "original_project_names": raw_titles,
            "customer_from_source": raw_customers[0],
            "original_customers": raw_customers,
            "start_month_from_source": periods[0],
            "end_month_from_source": periods[1],
            "roles_from_source": raw_roles,
            "source_row_evidence": [
                {
                    "row_id": s["row_id"], "document_id": s["document_id"],
                    "page_no": s["page_no"], "bbox": s["bbox"],
                    "period_cell": s.get("period_raw") or "",
                }
                for s in sources
            ],
            "candidate_indices": candidates,
            "candidate_evidence_reasons": reasons,
            "candidate_link_status": state,
            "review_name_variant_pair": gid in flagged,
            "safe_to_auto_confirm": False,
            "source_of_truth": "verified_pdf_native_table_row",
        }
        source_rows.append(row)
        redacted_groups.append({
            "group_id": gid,
            "candidate_count": len(candidates),
            "candidate_link_status": state,
            "review_name_variant_pair": gid in flagged,
        })

    unmatched = source_preview.get("candidate_unmatched")
    if not isinstance(unmatched, list):
        raise ValueError("CANDIDATE_UNMATCHED_MISSING")
    unmatched_ids = [e.get("index") for e in unmatched]
    if (
        any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in unmatched_ids)
        or len(unmatched_ids) != len(set(unmatched_ids))
        or set(unmatched_ids) & attached_indices
    ):
        raise ValueError("UNMATCHED_CANDIDATE_LINKAGE_INVALID")
    all_ids = attached_indices | set(unmatched_ids)
    if (
        len(all_ids) != source_preview.get("candidate_count")
        or all_ids != set(range(len(all_ids)))
        or len(attached_indices) != source_preview.get("candidates_attached_by_title_customer")
        or len(unmatched_ids) != source_preview.get("unmatched_candidate_count")
        or sum(len(r["candidate_indices"]) > 1 for r in source_rows)
        != source_preview.get("source_groups_with_multiple_candidates")
        or sum(not r["candidate_indices"] for r in source_rows)
        != source_preview.get("source_groups_without_candidate")
    ):
        raise ValueError("SOURCE_FIRST_SUMMARY_MISMATCH")
    # This produces a list of 36 ORIGINAL source group identities, never a
    # 33-project confirmation based on unreviewed name similarity.
    public = {
        "mode": "source_first_row_list_preview_readonly",
        "run_id": clusters["run_id"],
        "source_group_count": len(source_rows),
        "exact_cross_document_group_count": clusters.get("safe_multi_document_group_count"),
        "source_row_without_candidate_count": state_counts["source_row_without_candidate"],
        "multi_candidate_review_group_count": state_counts["multi_candidate_review"],
        "one_verified_candidate_group_count": state_counts["one_verified_candidate"],
        "one_candidate_evidence_review_group_count": state_counts["one_candidate_evidence_review"],
        "manual_name_variant_review_pair_count": len(pairs),
        "manual_name_variant_group_count": len(flagged),
        "unmatched_candidate_indices": unmatched_ids,
        "all_original_source_groups_preserved": True,
        "no_source_or_candidate_auto_merge": True,
        "no_model_or_db_calls": True,
        "group_list_redacted": redacted_groups,
        "review_pairs_redacted": [
            {"group_ids": pair["group_ids"],
             "candidate_indices": pair["candidate_indices"],
             "decision": "manual_review_only"}
            for pair in pairs
        ],
        "warning": (
            "Original PDF source groups are not confirmed profile projects. "
            "Candidate associations are tentative. Never treat an unmatched "
            "candidate as false or merge three suggested pairs automatically."
        ),
    }
    private = {
        "kind": "talentscope_private_source_first_rows_v30_preview",
        "run_id": clusters["run_id"],
        "source_groups": source_rows,
        "unmatched_candidate_indices": unmatched_ids,
        "review_pairs": pairs,
        "no_operational_writes": True,
    }
    return public, private


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--clusters-file", required=True, type=Path)
    p.add_argument("--source-first-report", required=True, type=Path)
    p.add_argument("--private-rows-output", type=Path)
    args = p.parse_args()
    clusters = read_report(args.clusters_file, CLUSTER_PASS)
    source = read_report(args.source_first_report, SOURCE_PASS)
    public, private = build_rows(clusters, source)

    if args.private_rows_output:
        # Local-only optional artifact. Refuse to overwrite existing
        # private material. 0600 permissions regardless of umask.
        fd = os.open(args.private_rows_output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(private, f, ensure_ascii=False, indent=2)
            f.write("\n")
        public["private_source_rows_written"] = True
    print(json.dumps(public, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
