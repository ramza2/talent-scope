#!/usr/bin/env python3
"""Compare v30 Source-First projects to the real revision snapshots, READ ONLY.

No user data, source text, project names, or quotation content is printed.
- Verify original stored candidate hash against the local source-first fixture.
- Query the specific AnalysisRun's *base* profile revision and the latest
  revision of the SAME person under a REPEATABLE READ / READ ONLY transaction.
- Re-evaluate source-first project identities with the unmodified Diff engine.
- Do not insert/update confirmed profiles, analysis runs, candidates, or diffs.

The current revision is a moving target: the report labels both revision IDs.
This is a comparison, not operational confirmation or source-identity merging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text

from app.db.models.revision import ProfileRevision
from app.db.session import SessionLocal
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.project_source_first_bridge_v30 import make_candidate_bridge
from app.modules.analysis.project_source_first_candidate_v30 import build_source_first_candidates
from scripts.profile_source_first_diff_preview_noai import report_diff

PASS = "TALENTSCOPE_V30_SOURCE_FIRST_LIVE_REVISION_READONLY_PASS"


def _fingerprint(payload: object) -> str:
    # Same canonical JSON hash contract as profile_prompt_ab_readonly.py.
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _project_categories(summary: dict[str, Any]) -> dict[str, Any]:
    from_spec = summary["root_diff_indices_only"]
    return {
        "represented_source_candidates": summary["represented_project_candidate_count"],
        "project_root_diff_count": summary["project_root_diff_count"],
        "root_change_types": summary["project_root_change_types"],
        "field_or_relation_diff_count": summary["project_field_or_relation_diff_count"],
        "candidate_indices_with_only_field_or_relation_diffs": summary[
            "project_candidate_indices_without_root_diff"
        ],
        "legacy_ungrounded_new_bulk_blocked": summary[
            "legacy_new_bulk_approval_guard_count"
        ],
        "root_with_original_quote": summary["project_root_with_literal_quote"],
        "root_indices_only": from_spec,
    }


def compare_revisions(
    source_first_candidate,
    *,
    base_snapshot: dict[str, Any],
    latest_snapshot: dict[str, Any],
    base_revision: int,
    latest_revision: int,
) -> dict[str, Any]:
    if (
        not isinstance(base_snapshot, dict) or not isinstance(latest_snapshot, dict)
        or not isinstance(base_snapshot.get("projects"), list)
        or not isinstance(latest_snapshot.get("projects"), list)
        or type(base_revision) is not int or type(latest_revision) is not int
        or base_revision < 1 or latest_revision < base_revision
    ):
        raise ValueError("REVISION_SNAPSHOT_INVALID")

    base = report_diff(source_first_candidate, base_snapshot)
    latest = report_diff(source_first_candidate, latest_snapshot)
    expected = len(source_first_candidate.projects)
    if (
        base["represented_project_candidate_count"] != expected
        or latest["represented_project_candidate_count"] != expected
    ):
        raise ValueError("DIFF_LOST_SOURCE_PROJECT")
    return {
        "source_project_count": expected,
        "base_revision": base_revision,
        "base_confirmed_project_count": len(base_snapshot["projects"]),
        "base_diff": _project_categories(base),
        "latest_revision": latest_revision,
        "latest_confirmed_project_count": len(latest_snapshot["projects"]),
        "latest_diff": _project_categories(latest),
        "scope": "READ_ONLY_PROJECT_DIFF_AGAINST_SAME_PERSON_REVISIONS",
        "no_merge_of_distinct_titles": True,
        "no_db_or_candidate_mutation": True,
        "no_llm_or_vlm_calls": True,
        "warning": (
            "Diff fallback may label potential same-customer/date name variants "
            "as REVIEW. Such review is informational, not a prerequisite "
            "to registration. This script never applies any review decision."
        ),
    }


def read_confirmed_revisions(run_id: UUID, expected_hash: str) -> tuple[dict, dict, int, int, dict]:
    """One isolated and read-only transaction; fetch ONLY one person's revisions."""
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        read_only = db.execute(text("SHOW transaction_read_only")).scalar_one()
        isolation = db.execute(text("SHOW transaction_isolation")).scalar_one()
        if read_only != "on" or isolation != "repeatable read":
            raise ValueError("DB_TRANSACTION_NOT_REPEATABLE_READ_ONLY")
        repo = AnalysisRepository(db)
        run = repo.get_run(run_id)
        if run is None or str(run.id) != str(run_id):
            raise ValueError("ANALYSIS_RUN_NOT_FOUND")
        if run.status not in {"CONFIRMED", "REVIEWING"}:
            raise ValueError("ANALYSIS_RUN_NOT_STABLE")
        if not isinstance(run.candidate_json, dict):
            raise ValueError("ANALYSIS_CANDIDATE_NOT_JSON")
        if _fingerprint(run.candidate_json) != expected_hash:
            raise ValueError("ORIGINAL_ANALYSIS_CANDIDATE_HASH_CHANGED")
        if not isinstance(run.base_profile_version, int) or run.base_profile_version < 1:
            raise ValueError("BASE_PROFILE_REVISION_NOT_DEFINED")
        base_revision = run.base_profile_version
        base = repo.get_profile_revision_snapshot(run.person_id, base_revision)
        latest = db.execute(
            select(ProfileRevision)
            .where(ProfileRevision.person_id == run.person_id)
            .order_by(ProfileRevision.revision_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None or not isinstance(latest.snapshot_json, dict):
            raise ValueError("LATEST_PROFILE_REVISION_NOT_FOUND")
        if latest.revision_no < base_revision:
            raise ValueError("LATEST_REVISION_BEFORE_ANALYSIS_BASE")
        if not isinstance(base, dict):
            raise ValueError("BASE_PROFILE_REVISION_NOT_FOUND")
        last_snapshot = dict(latest.snapshot_json)
        # Re-read the run fingerprint under the SAME repeatable-read snapshot.
        # DB session is closed without any commit or persisted changes.
        if _fingerprint(run.candidate_json) != expected_hash:
            raise ValueError("ANALYSIS_CANDIDATE_FINGERPRINT_DRIFT")
        return base, last_snapshot, base_revision, latest.revision_no, {
            "run_status": run.status,
            "run_prompt_version": run.prompt_version,
            "snapshot_isolation": isolation,
            "transaction_read_only": read_only,
        }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--private-preview", type=Path, required=True)
    p.add_argument("--private-source-rows", type=Path, required=True)
    p.add_argument("--private-ai-candidates", type=Path, required=True)
    args = p.parse_args()
    try:
        run_id = UUID(args.expected_run_id)
    except ValueError as exc:
        raise ValueError("EXPECTED_RUN_ID_MUST_BE_UUID") from exc

    preview = json.loads(args.private_preview.read_text(encoding="utf-8"))
    source_rows = json.loads(args.private_source_rows.read_text(encoding="utf-8"))
    ai = json.loads(args.private_ai_candidates.read_text(encoding="utf-8"))
    if (
        preview.get("run_id") != str(run_id)
        or source_rows.get("run_id") != str(run_id)
        or ai.get("run_id") != str(run_id)
        or not isinstance(ai.get("existing_candidate_sha256"), str)
        or len(ai["existing_candidate_sha256"]) != 64
    ):
        raise ValueError("READONLY_RUN_FIXTURE_MISMATCH")

    _, rebuilt_preview = build_source_first_candidates(source_rows, ai)
    if rebuilt_preview != preview:
        raise ValueError("READONLY_PREVIEW_MISMATCH")
    bridge = make_candidate_bridge(preview, ai)

    base, latest, base_revision, latest_revision, meta = read_confirmed_revisions(
        run_id, ai["existing_candidate_sha256"],
    )
    result = compare_revisions(
        bridge.candidate,
        base_snapshot=base,
        latest_snapshot=latest,
        base_revision=base_revision,
        latest_revision=latest_revision,
    )
    result.update({
        "run_id": str(run_id),
        "run_status": meta["run_status"],
        "run_prompt_version": meta["run_prompt_version"],
        "transaction_read_only": meta["transaction_read_only"],
        "snapshot_isolation": meta["snapshot_isolation"],
        "name_variant_pairs_kept_separate": bridge.diagnostics["name_variant_pair_count"],
        "source_role_groups_not_yet_mapped": bridge.diagnostics[
            "source_role_groups_not_mapped_to_job_codes"
        ],
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
