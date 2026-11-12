#!/usr/bin/env python3
"""Read-only v30 pilot: match previously extracted native PDF table rows.

Uses a local v29/row-grouping JSON report. No DB or AI client is imported,
no LLM/VLM requests, no candidate mutation. The optional model-input
artifact is JSON data, not a profile candidate or original quote evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.analysis.project_row_match_v30 import (
    build_clusters,
    build_llm_group_hints,
)

INPUT_MARKER = "TALENTSCOPE_PROJECT_ROWS_READONLY_PASS"
OUTPUT_MARKER = "TALENTSCOPE_V30_ROW_CLUSTERS_READONLY_PASS"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-candidate-sha", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--group-hints-output", type=Path)
    args = parser.parse_args()
    raw = args.input.read_text(encoding="utf-8")
    if not raw.rstrip().endswith(INPUT_MARKER):
        raise ValueError("INPUT_POC_SUCCESS_MARKER_MISSING")
    report = json.loads(raw.split(INPUT_MARKER, 1)[0])
    if report.get("run_id") != args.expected_run_id:
        raise ValueError("RUN_ID_MISMATCH")
    if report.get("candidate_sha256_unchanged") != args.expected_candidate_sha:
        raise ValueError("CANDIDATE_SHA_MISMATCH")
    if report.get("vlm_call_count") != 0 or report.get("vlm_opt_in") is not False:
        raise ValueError("REQUIRES_NATIVE_NO_VLM_INPUT")
    groups = report.get("groups")
    if not isinstance(groups, list):
        raise ValueError("PROJECT_ROW_GROUP_LIST_MISSING")
    result = build_clusters(groups)
    if result["input_rows"] != report.get("group_count"):
        raise ValueError("INPUT_GROUP_COUNT_MISMATCH")
    result["mode"] = "readonly_v30_row_cluster_preview"
    result["run_id"] = args.expected_run_id
    result["candidate_sha256_unchanged"] = args.expected_candidate_sha
    result["interpretation"] = (
        "Grouping hints only. No operational merge or LLM candidate write. "
        "Title variants remain separate until reviewed."
    )
    if args.group_hints_output:
        text = build_llm_group_hints(result)
        args.group_hints_output.write_text(text + "\n", encoding="utf-8")
        result["group_hints_chars"] = len(text)
        result["group_hints_path"] = str(args.group_hints_output)
    body = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(body + "\n" + OUTPUT_MARKER + "\n", encoding="utf-8")
    print(json.dumps(
        {key: val for key, val in result.items() if key != "groups"},
        ensure_ascii=False, indent=2,
    ))
    print(OUTPUT_MARKER)


if __name__ == "__main__":
    main()
