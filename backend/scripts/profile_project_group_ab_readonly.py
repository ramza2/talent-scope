#!/usr/bin/env python3
"""Opt-in PROJECTS-only v29 baseline vs v30 row-hints A/B, READ ONLY.

Both arms use exactly the same stored [DOCUMENT]/[PAGE] text and v29 pipeline.
Only experimental PROJECTS user prompt receives page-scoped table-row hints.
No VLM, AnalysisRun creation, candidate/diff persistence, or profile writes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.ai.prompts.profile_extract import resolve_profile_prompt
from app.core.config import get_settings
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.compact_v8 import build_document_alias_view
from app.modules.analysis.project_group_hints_v30 import (
    add_group_hints, scoped_group_data, validate_cluster_report,
)
from app.modules.analysis.service import AnalysisService
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage
from scripts.profile_project_reference import evaluate_projects, load_reference
from scripts.profile_prompt_ab_readonly import (
    MeteredLLM, _read_snapshot, _run_version, _verify_unchanged, project_metrics,
)


def load_cluster_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    marker = "TALENTSCOPE_V30_ROW_CLUSTERS_READONLY_PASS"
    if not text.rstrip().endswith(marker):
        raise ValueError("CLUSTER_SUCCESS_MARKER_MISSING")
    return json.loads(text.split(marker, 1)[0])


def _run_hint_arm(*, snapshot, prompt_source, settings, storage, run_id, groups,
                  alias_to_id: dict[str, str]) -> tuple:
    baseline = resolve_profile_prompt("profile-extract-v29")
    assert baseline.prompt_version == "profile-extract-v29"

    hint_counts: list[tuple[int, int]] = []
    original_builder = baseline.build_projects_user_prompt
    assert original_builder is not None

    def wrapped_user_prompt(*, code_catalog: str, document_blocks: str,
                            recovery_retry: bool = False) -> str:
        base = original_builder(
            code_catalog=code_catalog,
            document_blocks=document_blocks,
            recovery_retry=recovery_retry,
        )
        return add_group_hints(
            base, document_blocks=document_blocks,
            groups=groups, alias_to_id=alias_to_id,
            audit=lambda selected, chars: hint_counts.append((selected, chars)),
        )

    spec = replace(baseline, build_projects_user_prompt=wrapped_user_prompt)
    llm = MeteredLLM(settings)
    service = AnalysisService(None, storage=storage, settings=settings, llm=llm)
    service.repo = SimpleNamespace(
        list_active_codes=lambda: snapshot.codes,
        list_aliases_for_codes=lambda _codes: snapshot.aliases,
    )
    catalog_map = {
        code.code: (code.code_type, bool(code.is_active)) for code in snapshot.codes
    }
    document_blocks = prompt_source.text
    started = time.monotonic()
    candidate = service._extract_staged_compact_candidate(
        prompt=spec,
        document_blocks=document_blocks,
        catalog_map=catalog_map,
        allowed_documents=prompt_source.allowed_documents,
        page_texts=prompt_source.page_texts,
        documents=snapshot.documents,
        source_char_count=len(document_blocks.strip()),
        base_log={
            "analysis_run_id": str(run_id),
            "pilot": "v30_group_hint_ab_readonly",
            "prompt_version": "profile-extract-v29+row-groups",
            "document_count": len(snapshot.documents),
            "vlm_pages": 0,
            "source_char_count": len(document_blocks.strip()),
        },
        vlm_pages=0,
        projects_only=True,
    )
    document_types = {
        str(doc.id): str(doc.document_type_code)
        for doc in snapshot.documents if doc.document_type_code
    }
    guarded = apply_candidate_quality_guards(
        candidate,
        confirmed_person_name=snapshot.base_profile_name,
        page_texts=prompt_source.page_texts,
        document_type_by_id=document_types,
        strict_section_authority=spec.guard_core_section_authority,
    )
    if not any(chars > 0 for _, chars in hint_counts):
        raise ValueError("NO_GROUP_HINTS_ENTERED_PROJECT_PROMPTS")
    return guarded, {
        **project_metrics(guarded),
        "llm_calls": llm.json_calls,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "hint_prompt_build_count": sum(chars > 0 for _, chars in hint_counts),
        "max_groups_per_project_prompt": max((count for count, _ in hint_counts), default=0),
        "max_hint_chars_per_project_prompt": max((chars for _, chars in hint_counts), default=0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--clusters-file", required=True, type=Path)
    parser.add_argument("--ground-truth-file", type=Path)
    parser.add_argument("--live", action="store_true", help="Call LLM twice, no DB changes")
    parser.add_argument("--include-project-names", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    snapshot = _read_snapshot(args.run_id)
    if snapshot.run_status != "CONFIRMED":
        raise ValueError("REQUIRES_CONFIRMED_RUN")
    cluster_report = load_cluster_file(args.clusters_file)
    groups = validate_cluster_report(
        cluster_report, run_id=str(args.run_id),
        candidate_sha=snapshot.candidate_sha,
        known_documents={str(doc.id) for doc in snapshot.documents},
    )
    no_vlm_settings = settings.model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    storage = get_object_storage()
    builder = AnalysisSourceBuilder(storage, settings=no_vlm_settings, vlm=None)
    bundle = builder.build(
        list(snapshot.documents), log_context={"pilot": "v30_group_ab_readonly"}
    )
    assert not bundle.vlm_transcriptions and bundle.total_vlm_pages == 0
    source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
    assert source.text.strip(), "EMPTY_STORED_DOCUMENT_SOURCE"
    alias_source, alias_to_id = build_document_alias_view(source.text)
    if set(alias_to_id.values()) != {str(d.id) for d in snapshot.documents}:
        raise ValueError("DOCUMENT_ALIAS_SCOPE_MISMATCH")
    selected_full = scoped_group_data(
        groups, document_blocks=alias_source, alias_to_id=alias_to_id
    )
    if len(selected_full) != len(groups):
        raise ValueError("GROUP_HINTS_MISSING_IN_PROMPT_SOURCE")
    reference = load_reference(args.ground_truth_file) if args.ground_truth_file else None

    report = {
        "run_id": str(args.run_id),
        "mode": "live" if args.live else "preflight",
        "arms": ["v29_original", "v29_plus_v30_page_scoped_groups"],
        "evaluation_scope": "PROJECTS_only_no_CORE_no_VLM",
        "original_stored_pages": len(source.page_texts),
        "prompt_source_chars_unchanged": len(source.text),
        "source_sha256_unchanged": snapshot.documents_sha,
        "candidate_sha256_before_and_after": snapshot.candidate_sha,
        "input_group_count": len(groups),
        "safe_multi_document_group_count": cluster_report["safe_multi_document_group_count"],
        "groups_in_full_prompt": len(selected_full),
        "reference_status": reference["review_status"] if reference else None,
    }
    try:
        if args.live:
            baseline, baseline_stats = _run_version(
                version="profile-extract-v29",
                snapshot=snapshot, prompt_source=source,
                settings=settings, storage=storage, run_id=args.run_id,
            )
            enhanced, enhanced_stats = _run_hint_arm(
                snapshot=snapshot, prompt_source=source,
                settings=settings, storage=storage, run_id=args.run_id,
                groups=groups, alias_to_id=alias_to_id,
            )
            report["results"] = {
                "v29_original": baseline_stats,
                "v29_plus_v30_page_scoped_groups": enhanced_stats,
            }
            if reference is not None:
                a = evaluate_projects(
                    baseline, reference, include_names=args.include_project_names
                )
                b = evaluate_projects(
                    enhanced, reference, include_names=args.include_project_names
                )
                report["reference_evaluation"] = {
                    "v29_original": a,
                    "v29_plus_v30_page_scoped_groups": b,
                }
                report["comparison_delta"] = {
                    metric: b[metric] - a[metric]
                    for metric in (
                        "actual_count",
                        "matched_title_count",
                        "fully_correct_project_count",
                        "duplicate_title_count",
                        "missing_title_count",
                    )
                }
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("TALENTSCOPE_V30_PROJECT_GROUP_AB_READONLY_PASS")


if __name__ == "__main__":
    main()
