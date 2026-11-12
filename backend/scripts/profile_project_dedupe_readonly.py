#!/usr/bin/env python3
"""Read-only v30 compact LLM + conservative PDF row deduplication pilot.

One isolated compact-hints LLM replay in memory. It does NOT write candidate,
diff, original pages, confirmed profile, or a new AnalysisRun. Reports:
(1) raw candidate, (2) evidence-verified strict same-group union,
(3) counterfactual quarantine review only. Original data is never modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import get_settings
from app.modules.analysis.compact_v8 import build_document_alias_view
from app.modules.analysis.project_candidate_row_dedupe_v30 import preview_candidate_row_dedupe
from app.modules.analysis.project_group_hints_compact_v30 import add_compact_group_hints
from app.modules.analysis.project_group_hints_v30 import validate_cluster_report
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage
from scripts.profile_project_group_ab_readonly import _run_hint_arm, load_cluster_file
from scripts.profile_project_group_compact_readonly import MeasuredLLM, _read_previous
from scripts.profile_project_reference import evaluate_projects, load_reference
from scripts.profile_prompt_ab_readonly import _read_snapshot, _verify_unchanged


SUCCESS = "TALENTSCOPE_V30_DEDUPE_READONLY_PASS"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--previous-ab-result", required=True, type=Path)
    parser.add_argument("--previous-compact-result", required=True, type=Path)
    parser.add_argument("--ground-truth-file", required=True, type=Path)
    parser.add_argument("--live", action="store_true",
                        help="Replay the compact arm once, in memory only")
    args = parser.parse_args()

    snapshot = _read_snapshot(args.run_id)
    if snapshot.run_status != "CONFIRMED":
        raise ValueError("REQUIRES_CONFIRMED_RUN")
    settings = get_settings()
    reference = load_reference(args.ground_truth_file)
    prev_ab = _read_previous(
        args.previous_ab_result, run_id=str(args.run_id),
        source_sha=snapshot.documents_sha,
        candidate_sha=snapshot.candidate_sha,
        reference_status=reference["review_status"],
    )
    prior_raw = args.previous_compact_result.read_text(encoding="utf-8")
    marker = "TALENTSCOPE_V30_COMPACT_GROUP_READONLY_PASS"
    if not prior_raw.rstrip().endswith(marker):
        raise ValueError("PREVIOUS_COMPACT_SUCCESS_MARKER_MISSING")
    prior = json.loads(prior_raw.split(marker, 1)[0])
    if (
        prior.get("run_id") != str(args.run_id)
        or prior.get("source_sha256_unchanged") != snapshot.documents_sha
        or prior.get("candidate_sha256_before_and_after") != snapshot.candidate_sha
        or "optimized_results" not in prior
        or "optimized_reference_evaluation" not in prior
    ):
        raise ValueError("PREVIOUS_COMPACT_INPUT_OR_METRICS_MISMATCH")

    cluster_report = load_cluster_file(args.clusters_file)
    groups = validate_cluster_report(
        cluster_report, run_id=str(args.run_id),
        candidate_sha=snapshot.candidate_sha,
        known_documents={str(doc.id) for doc in snapshot.documents},
    )
    if len(groups) != prev_ab["input_group_count"]:
        raise ValueError("GROUP_COUNT_MISMATCH")

    no_vlm_settings = settings.model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    storage = get_object_storage()
    builder = AnalysisSourceBuilder(storage, settings=no_vlm_settings, vlm=None)
    bundle = builder.build(
        list(snapshot.documents), log_context={"pilot": "v30_row_dedupe_readonly"}
    )
    assert not bundle.vlm_transcriptions and bundle.total_vlm_pages == 0
    source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
    if not source.text.strip():
        raise ValueError("EMPTY_STORED_DOCUMENT_SOURCE")
    aliases_text, alias_to_id = build_document_alias_view(source.text)
    if set(alias_to_id.values()) != {str(doc.id) for doc in snapshot.documents}:
        raise ValueError("DOCUMENT_ALIAS_SCOPE_MISMATCH")
    probe = add_compact_group_hints(
        "", document_blocks=aliases_text, groups=groups, alias_to_id=alias_to_id
    )
    if "BEGIN UNTRUSTED ROW GROUP HINTS" not in probe:
        raise ValueError("COMPACT_GROUP_HINTS_MISSING")

    report = {
        "mode": "live" if args.live else "preflight",
        "run_id": str(args.run_id),
        "scope": "PROJECTS_only_existing_v29_pipeline_compact_v30_hints",
        "operation": "in_memory_strict_merge_and_review_quarantine_simulation",
        "original_page_count": len(source.page_texts),
        "input_group_count": len(groups),
        "source_sha256_unchanged": snapshot.documents_sha,
        "candidate_sha256_before_and_after": snapshot.candidate_sha,
        "reference_status": reference["review_status"],
        "previous_compact_baseline": {
            "project_count": prior["optimized_results"]["project_count"],
            "llm_calls": prior["optimized_results"]["llm_calls"],
            "elapsed_seconds": prior["optimized_results"]["elapsed_seconds"],
            "fully_correct_project_count": prior["optimized_reference_evaluation"]["fully_correct_project_count"],
            "duplicate_title_count": prior["optimized_reference_evaluation"]["duplicate_title_count"],
        },
        "no_write_no_auto_delete": True,
    }
    try:
        if args.live:
            candidate, stats = _run_hint_arm(
                snapshot=snapshot, prompt_source=source,
                settings=settings, storage=storage, run_id=args.run_id,
                groups=groups, alias_to_id=alias_to_id,
                hint_builder=add_compact_group_hints,
                llm_factory=MeasuredLLM,
            )
            preview = preview_candidate_row_dedupe(
                candidate, groups=groups, page_texts=source.page_texts
            )
            scored = {
                "raw": evaluate_projects(candidate, reference, include_names=False),
                "strict_evidence_merge": evaluate_projects(
                    preview.strict, reference, include_names=False
                ),
                "review_quarantine_simulation_only": evaluate_projects(
                    preview.quarantine_simulation, reference, include_names=False
                ),
            }
            report["llm_replay_stats"] = stats
            report["dedupe_diagnostics"] = preview.diagnostics
            report["reference_evaluation"] = scored
            report["deltas_vs_raw"] = {
                label: {
                    metric: value[metric] - scored["raw"][metric]
                    for metric in (
                        "actual_count",
                        "fully_correct_project_count",
                        "duplicate_title_count",
                        "missing_title_count",
                        "unexpected_title_count",
                    )
                }
                for label, value in scored.items() if label != "raw"
            }
            report["interpretation"] = (
                "Strict merge requires same unique source group/months and "
                "source-verified root quotes. Review-only quarantine must NEVER "
                "delete data; compare accuracy before considering changes."
            )
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(SUCCESS)


if __name__ == "__main__":
    main()
