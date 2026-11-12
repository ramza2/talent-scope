#!/usr/bin/env python3
"""Run native-PDF Source-First v30 preview directly from an EXISTING run.

The run must already be CONFIRMED. This performs:
  read original candidate & newest profile (read-only DB);
  open existing PDFs for native-table extraction (read-only object storage);
  cluster verified table cells, preserving distinct title variants;
  build v30 Candidate + existing Diff using v25 nonproject core;
  write only an optional LOCAL private 0600 JSON, never DB or S3.

No LLM/VLM calls. No AnalysisRun creation, review decisions or registration.
The output is deliberately NOT an operational AnalysisRun payload.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.analysis.project_source_first_native_direct_v30 import (
    assemble_native_direct,
)
from scripts.profile_project_rows_readonly import run as extract_rows
from scripts.profile_prompt_ab_readonly import _read_snapshot
from scripts.profile_source_first_review_payload_readonly import (
    read_latest_and_original_candidate,
)

PASS = "TALENTSCOPE_V30_NATIVE_DIRECT_READONLY_PASS"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--existing-confirmed-run-id", required=True)
    p.add_argument("--expected-candidate-sha", required=True)
    p.add_argument("--private-output", type=Path, required=True)
    args = p.parse_args()

    if args.private_output.exists():
        raise FileExistsError("NATIVE_V30_OUTPUT_ALREADY_EXISTS")
    if len(args.expected_candidate_sha) != 64 or any(
        c not in "0123456789abcdef" for c in args.expected_candidate_sha
    ):
        raise ValueError("NATIVE_V30_CANDIDATE_SHA_INVALID")
    try:
        run_id = UUID(args.existing_confirmed_run_id)
    except ValueError as exc:
        raise ValueError("NATIVE_V30_RUN_ID_INVALID") from exc

    original, latest, revisions = read_latest_and_original_candidate(
        run_id, args.expected_candidate_sha
    )
    if revisions.get("reference_run_status") != "CONFIRMED":
        raise ValueError("NATIVE_V30_SOURCE_RUN_MUST_BE_CONFIRMED")
    row_report = extract_rows(SimpleNamespace(
        run_id=run_id,
        expected_candidate_sha=args.expected_candidate_sha,
        allow_vlm=False,
        max_vlm_calls=0,
        include_rows=True,
    ))
    if (
        row_report.get("run_id") != str(run_id)
        or row_report.get("candidate_sha256_unchanged") !=
            args.expected_candidate_sha
        or row_report.get("vlm_opt_in") is not False
        or row_report.get("vlm_call_count") != 0
        or row_report.get("group_count") != len(row_report.get("groups") or [])
    ):
        raise ValueError("NATIVE_V30_ROW_EXTRACTION_NOT_VERIFIED")

    # Second detached snapshot remains read only; it carries the page text
    # for literal verification of historical AI root source_refs.
    snapshot = _read_snapshot(run_id)
    if (
        snapshot.candidate_sha != args.expected_candidate_sha
        or snapshot.run_status != "CONFIRMED"
    ):
        raise ValueError("NATIVE_V30_SOURCE_SNAPSHOT_CHANGED")
    page_texts = {
        (str(doc.id), page.page_no): page.extracted_text or ""
        for doc in snapshot.documents for page in doc.pages
    }
    assembled = assemble_native_direct(
        native_rows=row_report["groups"],
        original_candidate_json=original,
        latest_confirmed_snapshot=latest,
        page_texts=page_texts,
    )

    # Verify no source rows lost while regrouping (exact title/customer/date
    # cross-document groups are intentional; non-exact names stay distinct).
    if (
        assembled.diagnostics["candidate_project_count"] !=
            assembled.diagnostics["represented_project_candidates"]
        or assembled.diagnostics["native_pdf_input_rows"] !=
            row_report["group_count"]
    ):
        raise ValueError("NATIVE_V30_PROJECT_COVERAGE_NOT_COMPLETE")

    private = {
        "kind": "talentscope_v30_native_direct_private_local_preview",
        "source_run_id": str(run_id),
        "latest_revision_for_comparison_only": revisions[
            "target_confirmed_revision"
        ],
        "candidate_json": assembled.candidate.to_storage_dict(),
        "diff_specs_not_persisted": [x.to_persist_dict() for x in assembled.specs],
        "private_source_provenance": assembled.private_source_provenance,
        "not_operational_or_confirmable": True,
    }
    fd = os.open(args.private_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(private, out, ensure_ascii=False, indent=2, default=str)
        out.write("\n")
    public = {
        **assembled.diagnostics,
        "source_run_id": str(run_id),
        "source_run_prompt": revisions["reference_run_prompt"],
        "target_confirmed_revision": revisions["target_confirmed_revision"],
        "db_readonly": True,
        "vlm_calls": 0,
        "llm_calls": 0,
        "private_output_written": True,
    }
    print(json.dumps(public, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
