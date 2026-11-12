#!/usr/bin/env python3
"""No-AI v30 row-cell dedupe preview using the private project fixture.

Only aggregate/index-only diagnostics are printed. 47 raw project candidates
and a provisional reference remain in memory. The operational DB is used only
in read-only transactions with pre/post snapshot verification. No writes,
deletes, migrations, or LLM/VLM calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.core.config import get_settings
from app.modules.analysis.project_candidate_row_dedupe_v30 import preview_candidate_row_dedupe
from app.modules.analysis.project_group_hints_v30 import validate_cluster_report
from app.modules.analysis.project_row_cell_dedupe_v30 import preview_row_cell_merge
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage
from scripts.profile_project_group_ab_readonly import load_cluster_file
from scripts.profile_project_reference import evaluate_projects, load_reference
from scripts.profile_prompt_ab_readonly import _read_snapshot, _verify_unchanged


PASS = "TALENTSCOPE_V30_ROW_CELL_DEDUPE_READONLY_PASS"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--private-fixture", required=True, type=Path)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--ground-truth-file", required=True, type=Path)
    args = parser.parse_args()

    snapshot = _read_snapshot(args.run_id)
    if snapshot.run_status != "CONFIRMED":
        raise ValueError("REQUIRES_CONFIRMED_RUN")
    fixture = json.loads(args.private_fixture.read_text(encoding="utf-8"))
    if (
        fixture.get("kind") != "talentscope_readonly_project_candidate_fixture"
        or fixture.get("run_id") != str(args.run_id)
        or fixture.get("source_sha256") != snapshot.documents_sha
        or fixture.get("existing_candidate_sha256") != snapshot.candidate_sha
        or not isinstance(fixture.get("projects"), list)
    ):
        raise ValueError("PRIVATE_FIXTURE_MISMATCH")
    candidate = ProfileCandidateDocument.model_validate(
        {"projects": fixture["projects"]}
    )
    report = load_cluster_file(args.clusters_file)
    groups = validate_cluster_report(
        report, run_id=str(args.run_id),
        candidate_sha=snapshot.candidate_sha,
        known_documents={str(d.id) for d in snapshot.documents},
    )
    reference = load_reference(args.ground_truth_file)

    settings = get_settings().model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    builder = AnalysisSourceBuilder(get_object_storage(), settings=settings, vlm=None)
    try:
        bundle = builder.build(
            list(snapshot.documents), log_context={"pilot": "v30_row_cell_dedupe_readonly"}
        )
        if bundle.vlm_transcriptions or bundle.total_vlm_pages:
            raise ValueError("UNEXPECTED_VLM_IN_NO_AI_PILOT")
        source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
        baseline = preview_candidate_row_dedupe(
            candidate, groups=groups, page_texts=source.page_texts
        )
        improved = preview_row_cell_merge(
            candidate, groups=groups, page_texts=source.page_texts
        )
        comparison = {
            "raw": evaluate_projects(candidate, reference, include_names=False),
            "previous_strict": evaluate_projects(
                baseline.strict, reference, include_names=False
            ),
            "row_cell_strict_preview": evaluate_projects(
                improved.candidate, reference, include_names=False
            ),
        }
        base = comparison["raw"]
        after = comparison["row_cell_strict_preview"]
        if comparison["previous_strict"]["fully_correct_project_count"] < 0:
            raise AssertionError("INVALID_BASELINE")
        if improved.stats["merged_extra_count"] != (
            base["actual_count"] - after["actual_count"]
        ):
            raise ValueError("MERGE_COUNT_INCONSISTENT")
        if after["fully_correct_project_count"] < base["fully_correct_project_count"]:
            raise ValueError("ROW_CELL_MERGE_LOST_REFERENCE_COVERAGE")
        result = {
            "mode": "readonly_no_model_calls",
            "run_id": str(args.run_id),
            "reference_status": reference["review_status"],
            "source_sha256_unchanged": snapshot.documents_sha,
            "candidate_sha256_unchanged": snapshot.candidate_sha,
            "project_count": len(candidate.projects),
            "input_groups": len(groups),
            "no_llm_or_vlm_calls": True,
            "no_db_or_candidate_writes": True,
            "evaluations": comparison,
            "row_cell_dedupe": improved.stats,
            "delta_vs_raw": {
                key: after[key] - base[key]
                for key in ("actual_count", "fully_correct_project_count",
                            "duplicate_title_count", "unexpected_title_count",
                            "missing_title_count")
            },
        }
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
