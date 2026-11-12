#!/usr/bin/env python3
"""Read-only v30 Source-First adapter + existing quality guard/Diff comparison.

No DATABASE URL, repository, storage or LLM access. All inputs are previously
created PRIVATE read-only fixtures on the server. Nothing is stored in the
operational analysis candidate or confirmed project tables.

Default Diff baseline is the EMPTY profile, NOT the actual confirmed profile.
This tests shape/path/UI compatibility only, not true changed-vs-existing
classification. --base-snapshot accepts an explicitly furnished local JSON
snapshot when available, without opening the DB.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.diff_engine import build_diffs
from app.modules.analysis.diff_evidence import is_ungrounded_new_diff
from app.modules.analysis.project_source_first_bridge_v30 import make_candidate_bridge

PASS = "TALENTSCOPE_V30_SOURCE_FIRST_DIFF_PREVIEW_PASS"
ROOT_PATH = re.compile(r"^projects\[(\d+)\]$")


def report_diff(candidate, base_snapshot: dict[str, Any]) -> dict[str, Any]:
    specs = build_diffs(candidate, base_snapshot)
    roots = [
        spec for spec in specs
        if spec.entity_type == "PROJECT"
        and spec.field_name is None
        and ROOT_PATH.fullmatch(spec.candidate_path or "")
    ]
    root_indices = [int(ROOT_PATH.fullmatch(spec.candidate_path).group(1)) for spec in roots]
    if len(roots) != len(candidate.projects) or sorted(root_indices) != list(range(len(candidate.projects))):
        raise ValueError("DIFF_DROPPED_OR_DUPLICATED_SOURCE_ROOT")
    detail = []
    for spec in roots:
        idx = int(ROOT_PATH.fullmatch(spec.candidate_path).group(1))
        persisted = spec.to_persist_dict()
        grounded = bool(
            isinstance(persisted.get("new_value"), dict)
            and any(isinstance(r, dict) and r.get("quote_text")
                    for r in persisted["new_value"].get("source_refs", []))
        )
        ungrounded_new = is_ungrounded_new_diff(
            change_type=spec.change_type, new_value=persisted.get("new_value")
        )
        detail.append({
            "candidate_index": idx,
            "change_type": spec.change_type,
            "has_literal_root_quote": grounded,
            "legacy_new_bulk_approval_requires_individual_review": ungrounded_new,
        })
    return {
        "candidate_project_count": len(candidate.projects),
        "diff_total_count": len(specs),
        "project_root_diff_count": len(roots),
        "project_root_change_types": dict(Counter(s.change_type for s in roots)),
        "non_root_diff_count": len(specs) - len(roots),
        "project_root_with_literal_quote": sum(x["has_literal_root_quote"] for x in detail),
        "legacy_new_bulk_approval_guard_count": sum(
            x["legacy_new_bulk_approval_requires_individual_review"] for x in detail
        ),
        "note": (
            "The legacy non-simple bulk approval guard is measured; "
            "the simple_review API bypass is NOT invoked here. "
            "No change_type is forced or rewritten."
        ),
        "root_diff_indices_only": detail,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--private-preview", required=True, type=Path)
    p.add_argument("--private-ai-candidates", required=True, type=Path)
    p.add_argument("--base-snapshot", type=Path)
    p.add_argument("--private-candidate-output", type=Path)
    args = p.parse_args()
    if args.private_candidate_output and args.private_candidate_output.exists():
        raise FileExistsError("PRIVATE_CANDIDATE_OUTPUT_ALREADY_EXISTS")

    preview = json.loads(args.private_preview.read_text(encoding="utf-8"))
    ai = json.loads(args.private_ai_candidates.read_text(encoding="utf-8"))
    bridge = make_candidate_bridge(preview, ai)
    baseline = (json.loads(args.base_snapshot.read_text(encoding="utf-8"))
                if args.base_snapshot else {"projects": []})
    if not isinstance(baseline, dict):
        raise ValueError("BASE_SNAPSHOT_NOT_OBJECT")
    if not isinstance(baseline.get("projects", []), list):
        raise ValueError("BASE_PROJECTS_NOT_LIST")

    candidate = bridge.candidate
    guarded = apply_candidate_quality_guards(
        candidate, confirmed_person_name=None,
        page_texts=None, document_type_by_id=None,
        strict_section_authority=False,
    )
    strict_guarded = apply_candidate_quality_guards(
        candidate, confirmed_person_name=None,
        page_texts=None, document_type_by_id=None,
        strict_section_authority=True,
    )
    candidate_root_keys = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in candidate.projects
    ]
    guarding_keys = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in guarded.projects
    ]
    strict_keys = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in strict_guarded.projects
    ]
    # Keep going to surface the guard risk, not silently drop records.
    diff = report_diff(candidate, baseline)
    guarded_diff = report_diff(guarded, baseline) if guarded.projects else None
    outcome = {
        "mode": "source_first_v30_existing_candidate_diff_readonly",
        "run_id": bridge.diagnostics["run_id"],
        "baseline_type": "explicit_local_snapshot" if args.base_snapshot else "EMPTY_TEST_BASELINE_NOT_LIVE_PROFILE",
        "no_db_model_or_operational_writes": True,
        "source_first_bridge": bridge.diagnostics,
        "quality_guard": {
            "default_count": len(guarded.projects),
            "strict_section_authority_count_without_page_texts": len(strict_guarded.projects),
            "default_identity_preserved": candidate_root_keys == guarding_keys,
            "strict_identity_preserved_without_page_texts": candidate_root_keys == strict_keys,
            "warning": (
                "Real v25 guards run with stored page texts and person name; "
                "this offline check cannot establish live guard compatibility."
            ),
        },
        "existing_diff_empty_or_supplied_baseline": diff,
        "default_quality_guard_diff": guarded_diff,
        "no_name_variant_auto_merge": True,
        "no_project_enrichment_auto_promoted": True,
        "no_source_quote_fabrication": True,
        "not_an_operational_profile_candidate_write": True,
    }
    if args.private_candidate_output:
        # Only the source-first root candidate JSON; never mix with personal
        # profile or existing confirmed records, no hidden DB work.
        private = {
            "kind": "talentscope_private_source_first_candidate_diff_bridge_v30",
            "run_id": bridge.diagnostics["run_id"],
            "base_snapshot_type": outcome["baseline_type"],
            "candidate": candidate.to_storage_dict(),
        }
        fd = os.open(args.private_candidate_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(private, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        outcome["private_candidate_written"] = True
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
