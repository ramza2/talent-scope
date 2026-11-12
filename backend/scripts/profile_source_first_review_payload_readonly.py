#!/usr/bin/env python3
"""Build v30 source-first Candidate+Diff UI-shape preview, strictly read-only.

Uses already verified local source-first/AI files AND historical source run.
Loads original candidate/core and latest confirmed revision in a single
REPEATABLE READ READ ONLY transaction. Outputs private, local-only JSON
(0600), never inserts AnalysisRun, AnalysisDiffItem or anything else.

NOT a review API response: Diff items have no live IDs, Evidence rows, or
decisions, and cannot be confirmed until an isolated v30 run is implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text

from app.db.models.revision import ProfileRevision
from app.db.session import SessionLocal
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.project_source_first_review_payload_v30 import (
    assemble_review_payload,
)

PASS = "TALENTSCOPE_V30_REVIEW_PAYLOAD_READONLY_PASS"


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def read_latest_and_original_candidate(run_id: UUID, candidate_sha: str):
    """Read the run and its person's newest revision with NO writes."""
    if len(candidate_sha) != 64:
        raise ValueError("V30_REVIEW_BAD_CANDIDATE_FINGERPRINT")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        if (
            db.execute(text("SHOW transaction_read_only")).scalar_one() != "on"
            or db.execute(text("SHOW transaction_isolation")).scalar_one()
            != "repeatable read"
        ):
            raise ValueError("V30_REVIEW_DB_NOT_READ_ONLY")
        repo = AnalysisRepository(db)
        run = repo.get_run(run_id)
        if run is None or run.status not in {"REVIEWING", "CONFIRMED"}:
            raise ValueError("V30_REVIEW_REFERENCE_RUN_NOT_STABLE")
        if not isinstance(run.candidate_json, dict):
            raise ValueError("V30_REVIEW_REFERENCE_CANDIDATE_INVALID")
        if fingerprint(run.candidate_json) != candidate_sha:
            raise ValueError("V30_REVIEW_REFERENCE_CANDIDATE_SHA_MISMATCH")
        if (
            type(run.base_profile_version) is not int
            or run.base_profile_version < 1
        ):
            raise ValueError("V30_REVIEW_BASE_REVISION_INVALID")

        newest = db.execute(
            select(ProfileRevision)
            .where(ProfileRevision.person_id == run.person_id)
            .order_by(ProfileRevision.revision_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        if (
            newest is None
            or type(newest.revision_no) is not int
            or newest.revision_no < run.base_profile_version
            or not isinstance(newest.snapshot_json, dict)
            or not isinstance(newest.snapshot_json.get("projects"), list)
        ):
            raise ValueError("V30_REVIEW_NEWEST_REVISION_INVALID")

        # The same read transaction covers the run candidate and newest
        # confirmed revision: no time-of-check/time-of-use drift within it.
        return (
            dict(run.candidate_json),
            dict(newest.snapshot_json),
            {
                "reference_run_status": run.status,
                "reference_run_prompt": run.prompt_version,
                "reference_run_base_revision": run.base_profile_version,
                "target_confirmed_revision": newest.revision_no,
                "transaction_read_only": "on",
                "transaction_isolation": "repeatable read",
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--private-source-rows", type=Path, required=True)
    parser.add_argument("--private-ai-candidates", type=Path, required=True)
    parser.add_argument("--private-preview", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()

    if args.private_output.exists():
        raise FileExistsError("V30_REVIEW_PRIVATE_OUTPUT_ALREADY_EXISTS")
    try:
        run_id = UUID(args.expected_run_id)
    except ValueError:
        raise ValueError("V30_REVIEW_RUN_ID_INVALID") from None

    source = json.loads(args.private_source_rows.read_text(encoding="utf-8"))
    ai = json.loads(args.private_ai_candidates.read_text(encoding="utf-8"))
    preview = json.loads(args.private_preview.read_text(encoding="utf-8"))
    if any(data.get("run_id") != str(run_id) for data in (source, ai, preview)):
        raise ValueError("V30_REVIEW_RUN_ID_NOT_SAME")
    if (
        not isinstance(ai.get("existing_candidate_sha256"), str)
        or len(ai["existing_candidate_sha256"]) != 64
    ):
        raise ValueError("V30_REVIEW_FIXTURE_CANDIDATE_HASH_INVALID")

    original, newest, revisions = read_latest_and_original_candidate(
        run_id, ai["existing_candidate_sha256"]
    )
    assembled = assemble_review_payload(
        source_rows=source,
        ai_fixture=ai,
        private_preview=preview,
        original_candidate_json=original,
        current_profile_snapshot=newest,
    )
    if (
        assembled.diagnostics["project_candidate_count"] != len(
            source["source_groups"]
        )
        or assembled.diagnostics["project_indices_represented_in_diff"]
        != len(source["source_groups"])
    ):
        raise ValueError("V30_REVIEW_PROJECTS_NOT_FULLY_REPRESENTED")

    # Do not print profile names, project names, source refs, free text
    # or confidential before/after values to the console.
    payload = {
        "kind": "talentscope_private_v30_local_review_payload_not_operational",
        "source_run_id": str(run_id),
        "run_status": "LOCAL_PREVIEW_ONLY",
        "target_confirmed_profile_revision": revisions[
            "target_confirmed_revision"
        ],
        "project_count": len(assembled.candidate.projects),
        "candidate_json": assembled.candidate.to_storage_dict(),
        "diffs_without_live_ids": [
            diff.to_persist_dict() for diff in assembled.diffs
        ],
        "must_not_submit_as_operational_confirmation": True,
        "source_first_groups_remain_distinct": True,
    }
    fd = os.open(args.private_output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")

    public = {
        "mode": "v30_source_first_project_review_payload_readonly",
        "source_run_id": str(run_id),
        **revisions,
        **assembled.diagnostics,
        "private_review_payload_written": True,
        "private_review_payload_not_operational": True,
    }
    print(json.dumps(public, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
