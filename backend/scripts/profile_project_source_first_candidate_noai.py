#!/usr/bin/env python3
"""Generate v30 Source-First PROJECTS preview from existing local-only files.

No LLM, VLM, API, Docker or DB access. Compares private native source rows
against the independently generated cluster+coverage reports before use.
Project names, source cells and AI suggestions are written only to the
explicitly requested new private 0600 file. Console contains indices/counts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.analysis.project_source_first_candidate_v30 import (
    build_source_first_candidates,
)
from scripts.profile_project_source_first_preview_noai import (
    CLUSTER_PASS, PASS as SOURCE_PASS, read_report,
)
from scripts.profile_project_source_first_rows_noai import build_rows

PASS = "TALENTSCOPE_V30_SOURCE_FIRST_CANDIDATE_PREVIEW_PASS"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-source-rows", required=True, type=Path)
    parser.add_argument("--private-ai-candidates", required=True, type=Path)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--source-first-report", required=True, type=Path)
    parser.add_argument("--private-preview-output", required=True, type=Path)
    args = parser.parse_args()

    if args.private_preview_output.exists():
        raise FileExistsError("PRIVATE_PREVIEW_ALREADY_EXISTS")
    clusters = read_report(args.clusters_file, CLUSTER_PASS)
    covered = read_report(args.source_first_report, SOURCE_PASS)
    _, expected_rows = build_rows(clusters, covered)
    supplied_rows = json.loads(args.private_source_rows.read_text(encoding="utf-8"))
    if supplied_rows != expected_rows:
        raise ValueError("PRIVATE_NATIVE_SOURCE_ROWS_DIVERGE_FROM_VERIFIED_CLUSTER_REPORT")

    ai = json.loads(args.private_ai_candidates.read_text(encoding="utf-8"))
    if (
        ai.get("run_id") != clusters.get("run_id")
        or ai.get("existing_candidate_sha256") != clusters.get("candidate_sha256_unchanged")
        or not isinstance(ai.get("source_sha256"), str)
        or not ai["source_sha256"]
    ):
        raise ValueError("AI_CANDIDATE_SOURCE_FINGERPRINT_MISMATCH")

    public, private = build_source_first_candidates(supplied_rows, ai)
    if (
        public["native_project_group_count"] != clusters["group_count"]
        or public["proposed_project_count"] != clusters["group_count"]
        or public["name_variant_pair_count"] != covered["review_only_pair_count"]
        or public["unlinked_ai_candidate_count"] != covered["unmatched_candidate_count"]
        or public["candidate_enrichment_suggestion_count"] !=
            covered["candidates_attached_by_title_customer"]
    ):
        raise ValueError("SOURCE_FIRST_OUTPUT_COUNT_MISMATCH")

    # Create only an opt-in local output. Never overwrite existing
    # candidate or profile data. The file is 0600, not committed to Git.
    fd = os.open(
        args.private_preview_output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(private, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    public["private_source_first_preview_written"] = True
    print(json.dumps(public, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
