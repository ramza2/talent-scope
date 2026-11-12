#!/usr/bin/env python3
"""Dry-run by DEFAULT: v30 Source-First -> isolated existing 6-tab review UI.

The source is an existing CONFIRMED v25 AnalysisRun and its original PDF
documents. The new v30 run reuses v25 nonproject CORE, but replaces project
candidates with verified native PDF rows. It does NOT rerun AI.

Default (no opt-in switch):
  reads existing run, PDF and profile; prints only aggregate preflight counts;
  writes NOTHING to DB/S3 and creates NO operational AnalysisRun.

Explicit --execute-create-review-run + --acknowledge-create-review-run:
  in a single short transaction: rechecks source checksum, person/documents,
  newest profile version, source bindings, active/duplicate analyses;
  then inserts ONE separate REVIEWING analysis with Candidate+Diffs.
  Does NOT review, approve or confirm anything.

This is a controlled pilot CLI, not installed into v25 auto-analysis or API.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.project_source_first_native_direct_v30 import (
    assemble_native_direct,
)
from app.modules.analysis.project_source_first_review_run_v30 import (
    create_plan, persist_reviewing_run,
)
from scripts.profile_project_rows_readonly import run as extract_rows
from scripts.profile_prompt_ab_readonly import _read_snapshot
from scripts.profile_source_first_review_payload_readonly import (
    read_latest_and_original_candidate,
)

PASS = "TALENTSCOPE_V30_REVIEW_RUN_PREFLIGHT_PASS"
CREATED = "TALENTSCOPE_V30_REVIEW_RUN_CREATED"


def _read_person_id(run_id: UUID) -> UUID:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        if db.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise ValueError("V30_RUN_SOURCE_DB_NOT_READ_ONLY")
        run = AnalysisRepository(db).get_run(run_id)
        if run is None or run.status != "CONFIRMED":
            raise ValueError("V30_RUN_SOURCE_NOT_CONFIRMED")
        return run.person_id


def assemble_preflight(
    *,
    source_run_id: UUID,
    expected_candidate_sha: str,
    expected_revision: int,
    expected_project_count: int,
):
    if expected_project_count < 1 or expected_revision < 1:
        raise ValueError("V30_PREFLIGHT_EXPECTED_COUNTS_INVALID")
    original, latest, revisions = read_latest_and_original_candidate(
        source_run_id, expected_candidate_sha
    )
    if (
        revisions["reference_run_status"] != "CONFIRMED"
        or revisions["target_confirmed_revision"] != expected_revision
    ):
        raise ValueError("V30_PREFLIGHT_PROFILE_VERSION_CHANGED")

    # Source PDF extraction invokes the previously validated scanner and
    # verifies no source/candidate mutation. VLM explicitly OFF.
    result = extract_rows(SimpleNamespace(
        run_id=source_run_id,
        expected_candidate_sha=expected_candidate_sha,
        allow_vlm=False,
        max_vlm_calls=0,
        include_rows=True,
    ))
    if (
        result.get("run_id") != str(source_run_id)
        or result.get("candidate_sha256_unchanged") != expected_candidate_sha
        or result.get("vlm_call_count") != 0
        or result.get("vlm_opt_in") is not False
        or result.get("group_count") != len(result.get("groups") or [])
    ):
        raise ValueError("V30_PREFLIGHT_NATIVE_EXTRACTION_INVALID")
    snapshot = _read_snapshot(source_run_id)
    if (
        snapshot.run_status != "CONFIRMED"
        or snapshot.candidate_sha != expected_candidate_sha
        or not snapshot.documents
    ):
        raise ValueError("V30_PREFLIGHT_SOURCE_SNAPSHOT_DRIFT")

    page_texts = {
        (str(doc.id), page.page_no): page.extracted_text or ""
        for doc in snapshot.documents for page in doc.pages
    }
    assembled = assemble_native_direct(
        native_rows=result["groups"],
        original_candidate_json=original,
        latest_confirmed_snapshot=latest,
        page_texts=page_texts,
    )
    diagnostics = assembled.diagnostics
    if (
        diagnostics["candidate_project_count"] != expected_project_count
        or diagnostics["represented_project_candidates"] != expected_project_count
        or diagnostics["source_roles_preserved_count"] != expected_project_count
        or diagnostics["default_guard_preserves_all_native_projects"] is not True
    ):
        raise ValueError("V30_PREFLIGHT_PROJECT_COUNT_OR_ROLES_UNSAFE")

    plan = create_plan(
        original_run_id=source_run_id,
        original_candidate_sha=expected_candidate_sha,
        person_id=_read_person_id(source_run_id),
        document_ids=tuple(doc.id for doc in snapshot.documents),
        base_profile_version=expected_revision,
        latest_snapshot=latest,
        candidate=assembled.candidate,
        diff_specs=assembled.specs,
        source_group_count=diagnostics["candidate_project_count"],
        role_group_count=diagnostics["source_roles_preserved_count"],
        blocked_variant_links=diagnostics["title_variant_auto_links_blocked"],
        blocked_collision_links=diagnostics["duplicate_target_auto_links_blocked"],
    )
    return plan, diagnostics, revisions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-confirmed-run-id", required=True)
    parser.add_argument("--expected-candidate-sha", required=True)
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--expected-project-count", required=True, type=int)
    parser.add_argument(
        "--execute-create-review-run", action="store_true",
        help="WRITES one new REVIEWING AnalysisRun + Diff rows to the DB"
    )
    parser.add_argument(
        "--acknowledge-create-review-run", default="",
        help="Requires EXACT value CREATE_V30_REVIEWING_RUN with --execute"
    )
    args = parser.parse_args()
    try:
        run_id = UUID(args.existing_confirmed_run_id)
    except ValueError:
        raise ValueError("V30_PREFLIGHT_RUN_ID_INVALID") from None
    ack = args.acknowledge_create_review_run
    if args.execute_create_review_run != (ack == "CREATE_V30_REVIEWING_RUN"):
        raise ValueError("V30_EXECUTION_FLAG_AND_ACKNOWLEDGEMENT_REQUIRED_TOGETHER")
    if not args.execute_create_review_run and ack:
        raise ValueError("V30_PREFLIGHT_UNEXPECTED_WRITE_ACKNOWLEDGEMENT")

    plan, diagnostics, meta = assemble_preflight(
        source_run_id=run_id,
        expected_candidate_sha=args.expected_candidate_sha,
        expected_revision=args.expected_revision,
        expected_project_count=args.expected_project_count,
    )
    output = {
        "source_run_id": str(run_id),
        "base_profile_revision": plan.base_profile_version,
        "native_pdf_source_rows": diagnostics["native_pdf_input_rows"],
        "native_project_groups": diagnostics["native_project_groups"],
        "candidate_projects": diagnostics["candidate_project_count"],
        "source_roles_preserved": diagnostics["source_roles_preserved_count"],
        "project_diff_change_types": diagnostics["project_diff_type_counts"],
        "total_diff_items": len(plan.diff_specs),
        "title_variant_links_blocked": plan.blocked_variant_links,
        "duplicate_target_links_blocked": plan.blocked_collision_links,
        "source_v25_core_preserved": diagnostics["nonproject_candidate_unchanged"],
        "no_llm_vlm_calls": True,
        "note": (
            "Source run CORE is reused from prior v25, not freshly reanalyzed. "
            "Project rows and Diff baseline are regenerated and verified."
        ),
    }
    if not args.execute_create_review_run:
        output["mode"] = "READ_ONLY_NO_ANALYSIS_RUN_CREATED"
        output["database_mutations"] = 0
        print(json.dumps(output, ensure_ascii=False, indent=2))
        print(PASS)
        return

    # All external reads/PDF work finished. One short transaction rechecks
    # source, profile and documents under locks before writing anything.
    with SessionLocal() as db:
        with db.begin():
            new_id = persist_reviewing_run(
                db, plan,
                expected_revision=args.expected_revision,
                expected_project_count=args.expected_project_count,
            )
    output.update({
        "mode": "OPT_IN_NEW_REVIEWING_ANALYSIS_CREATED",
        "new_analysis_run_id": str(new_id),
        "review_status": "REVIEWING",
        "profile_confirmation_performed": False,
        "existing_run_changed": False,
    })
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print(CREATED)


if __name__ == "__main__":
    main()
