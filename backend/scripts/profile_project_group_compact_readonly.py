#!/usr/bin/env python3
"""Read-only v30 COMPACT project-hint experiment; ONE LLM arm per --live run.

Compare with a previously saved v29/full-hints A/B report; do not replay
baseline. No VLM/DB writes/operational candidate changes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import get_settings
from app.modules.analysis.compact_v8 import build_document_alias_view
from app.modules.analysis.project_group_hints_compact_v30 import add_compact_group_hints
from app.modules.analysis.project_group_hints_v30 import validate_cluster_report
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.modules.analysis.token_budget import split_block_by_pages, split_document_blocks
from app.storage.s3 import get_object_storage
from scripts.profile_project_group_ab_readonly import load_cluster_file, _run_hint_arm
from scripts.profile_project_reference import evaluate_projects, load_reference
from scripts.profile_prompt_ab_readonly import MeteredLLM, _read_snapshot, _verify_unchanged

_SUCCESS = "TALENTSCOPE_V30_COMPACT_GROUP_READONLY_PASS"
_PREVIOUS_SUCCESS = "TALENTSCOPE_V30_PROJECT_GROUP_AB_READONLY_PASS"


class MeasuredLLM(MeteredLLM):
    """Counts only aggregate tokens/chunk IDs/timing; never stores prompts."""
    def __init__(self, settings):
        super().__init__(settings)
        self.call_details: list[dict] = []

    def complete_json(self, **kwargs):
        context = kwargs.get("log_context") or {}
        started = time.monotonic()
        state = "success"
        try:
            return super().complete_json(**kwargs)
        except Exception as exc:
            state = type(exc).__name__
            raise
        finally:
            self.call_details.append({
                "phase": context.get("phase"),
                "split_depth": context.get("split_depth"),
                "chunk_index": context.get("chunk_index"),
                "recovery_retry": bool(context.get("recovery_retry", False)),
                "prompt_tokens": context.get("prompt_tokens"),
                "max_completion_tokens": context.get("max_completion_tokens"),
                "outcome": state,
                "elapsed_seconds": round(time.monotonic() - started, 2),
            })


def _read_previous(path: Path, *, run_id: str, source_sha: str, candidate_sha: str,
                   reference_status: str) -> dict:
    raw = path.read_text(encoding="utf-8")
    if not raw.rstrip().endswith(_PREVIOUS_SUCCESS):
        raise ValueError("PREVIOUS_AB_SUCCESS_MARKER_MISSING")
    previous = json.loads(raw.split(_PREVIOUS_SUCCESS, 1)[0])
    if (previous.get("run_id") != run_id
        or previous.get("source_sha256_unchanged") != source_sha
        or previous.get("candidate_sha256_before_and_after") != candidate_sha
        or previous.get("reference_status") != reference_status):
        raise ValueError("PREVIOUS_AB_RUN_OR_SOURCE_MISMATCH")
    if set((previous.get("results") or {}).keys()) != {
        "v29_original", "v29_plus_v30_page_scoped_groups"
    }:
        raise ValueError("PREVIOUS_AB_ARMS_MISMATCH")
    if set((previous.get("reference_evaluation") or {}).keys()) != {
        "v29_original", "v29_plus_v30_page_scoped_groups"
    }:
        raise ValueError("PREVIOUS_AB_REFERENCE_EVALUATION_MISSING")
    return previous


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--previous-ab-result", required=True, type=Path)
    parser.add_argument("--ground-truth-file", required=True, type=Path)
    parser.add_argument("--live", action="store_true",
                        help="One compact-hints LLM replay; no persistence")
    args = parser.parse_args()

    settings = get_settings()
    snapshot = _read_snapshot(args.run_id)
    if snapshot.run_status != "CONFIRMED":
        raise ValueError("REQUIRES_CONFIRMED_RUN")
    reference = load_reference(args.ground_truth_file)
    previous = _read_previous(
        args.previous_ab_result, run_id=str(args.run_id),
        source_sha=snapshot.documents_sha,
        candidate_sha=snapshot.candidate_sha,
        reference_status=reference["review_status"],
    )

    cluster_report = load_cluster_file(args.clusters_file)
    groups = validate_cluster_report(
        cluster_report, run_id=str(args.run_id),
        candidate_sha=snapshot.candidate_sha,
        known_documents={str(d.id) for d in snapshot.documents},
    )
    if len(groups) != previous["input_group_count"]:
        raise ValueError("PREVIOUS_AB_GROUP_COUNT_MISMATCH")

    no_vlm_settings = settings.model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    storage = get_object_storage()
    builder = AnalysisSourceBuilder(storage, settings=no_vlm_settings, vlm=None)
    bundle = builder.build(
        list(snapshot.documents), log_context={"pilot": "v30_compact_groups_readonly"}
    )
    assert not bundle.vlm_transcriptions and bundle.total_vlm_pages == 0
    source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
    if not source.text.strip():
        raise ValueError("EMPTY_STORED_DOCUMENT_SOURCE")
    alias_source, alias_to_id = build_document_alias_view(source.text)
    if set(alias_to_id.values()) != {str(d.id) for d in snapshot.documents}:
        raise ValueError("DOCUMENT_ALIAS_SCOPE_MISMATCH")

    prompt_preview = add_compact_group_hints(
        "ORIGINAL_SOURCE_UNCHANGED", document_blocks=alias_source,
        groups=groups, alias_to_id=alias_to_id,
    )
    if "BEGIN UNTRUSTED ROW GROUP HINTS" not in prompt_preview:
        raise ValueError("NO_COMPACT_HINTS_IN_SOURCE")
    previous_max_hint = previous["results"]["v29_plus_v30_page_scoped_groups"]["max_hint_chars_per_project_prompt"]
    compact_full_hint_chars = len(prompt_preview) - len("ORIGINAL_SOURCE_UNCHANGED")
    if compact_full_hint_chars >= previous_max_hint:
        raise ValueError("COMPACT_HINT_NOT_SMALLER_THAN_PREVIOUS")
    page_previews = []
    for document in split_document_blocks(alias_source):
        for page in split_block_by_pages(document):
            lengths: list[tuple[int, int]] = []
            add_compact_group_hints(
                "", document_blocks=page,
                groups=groups, alias_to_id=alias_to_id,
                audit=lambda count, chars: lengths.append((count, chars)),
            )
            page_previews.extend(lengths)
    report = {
        "run_id": str(args.run_id),
        "mode": "live" if args.live else "preflight",
        "evaluation_scope": "PROJECTS_only_same_v29_pipeline_no_CORE_no_VLM",
        "source_sha256_unchanged": snapshot.documents_sha,
        "candidate_sha256_before_and_after": snapshot.candidate_sha,
        "input_groups": len(groups),
        "full_source_compact_hint_chars": compact_full_hint_chars,
        "page_hint_preview": [
            {"groups": count, "hint_payload_chars": chars}
            for count, chars in page_previews
        ],
        "historical_baselines": {
            key: {
                "results": previous["results"][key],
                "reference_evaluation": previous["reference_evaluation"][key],
            }
            for key in ("v29_original", "v29_plus_v30_page_scoped_groups")
        },
        "interpretation": (
            "Historical baseline comparisons are approximate across separate model "
            "replays. Ground truth is provisional. No automatic project writes."
        ),
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
            evaluation = evaluate_projects(candidate, reference, include_names=False)
            report["optimized_results"] = stats
            report["optimized_reference_evaluation"] = evaluation
            report["comparison_to_previous"] = {}
            for label in ("v29_original", "v29_plus_v30_page_scoped_groups"):
                prior_evaluation = previous["reference_evaluation"][label]
                prior_results = previous["results"][label]
                report["comparison_to_previous"][label] = {
                    "delta_full_correct": (
                        evaluation["fully_correct_project_count"]
                        - prior_evaluation["fully_correct_project_count"]
                    ),
                    "delta_duplicate_titles": (
                        evaluation["duplicate_title_count"]
                        - prior_evaluation["duplicate_title_count"]
                    ),
                    "delta_project_count": (
                        evaluation["actual_count"] - prior_evaluation["actual_count"]
                    ),
                    "delta_llm_calls": stats["llm_calls"] - prior_results["llm_calls"],
                    "delta_elapsed_seconds": round(
                        stats["elapsed_seconds"] - prior_results["elapsed_seconds"], 1
                    ),
                }
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(_SUCCESS)


if __name__ == "__main__":
    main()
