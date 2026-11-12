#!/usr/bin/env python3
"""Explain v30 candidate duplicate blockers without an LLM/VLM replay.

Reads the PRIVATE 0600 project fixture and stored source pages of an existing
confirmed run. Produces index-only diagnostics: no names/quotes/full projects.
Never persists a candidate, diff, document, or profile.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.core.config import get_settings
from app.modules.analysis.project_dedupe_diagnostics_v30 import diagnose
from app.modules.analysis.project_group_hints_v30 import validate_cluster_report
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage
from scripts.profile_project_group_ab_readonly import load_cluster_file
from scripts.profile_project_reference import evaluate_projects, load_reference
from scripts.profile_prompt_ab_readonly import _read_snapshot, _verify_unchanged

PASS = "TALENTSCOPE_V30_DEDUPE_REASON_READONLY_PASS"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", type=UUID, required=True)
    p.add_argument("--private-fixture", type=Path, required=True)
    p.add_argument("--clusters-file", type=Path, required=True)
    p.add_argument("--ground-truth-file", type=Path, required=True)
    args = p.parse_args()

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
        raise ValueError("PRIVATE_FIXTURE_SOURCE_OR_FORMAT_MISMATCH")
    candidate = ProfileCandidateDocument.model_validate(
        {"projects": fixture["projects"]}
    )
    cluster_report = load_cluster_file(args.clusters_file)
    groups = validate_cluster_report(
        cluster_report, run_id=str(args.run_id),
        candidate_sha=snapshot.candidate_sha,
        known_documents={str(doc.id) for doc in snapshot.documents},
    )
    reference = load_reference(args.ground_truth_file)
    settings = get_settings().model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    builder = AnalysisSourceBuilder(get_object_storage(), settings=settings, vlm=None)
    try:
        bundle = builder.build(
            list(snapshot.documents),
            log_context={"pilot": "v30_dedupe_reason_no_model_calls"},
        )
        assert not bundle.vlm_transcriptions and bundle.total_vlm_pages == 0
        source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
        details = diagnose(
            candidate, groups=groups, page_texts=source.page_texts,
            reference=reference,
        )
        evaluation = evaluate_projects(candidate, reference, include_names=False)
        if details["total_candidate_count"] != evaluation["actual_count"]:
            raise ValueError("CANDIDATE_COUNT_DISAGREEMENT")
        if details["reference_duplicate_extras"] != evaluation["duplicate_title_count"]:
            raise ValueError("DUPLICATE_EVALUATION_COUNT_DISAGREEMENT")
        result = {
            "run_id": str(args.run_id),
            "source_sha256_unchanged": snapshot.documents_sha,
            "candidate_sha256_unchanged": snapshot.candidate_sha,
            "reference_status": reference["review_status"],
            "project_count": evaluation["actual_count"],
            "fully_correct_project_count": evaluation["fully_correct_project_count"],
            "duplicate_title_count": evaluation["duplicate_title_count"],
            "unexpected_title_count": evaluation["unexpected_title_count"],
            "no_model_calls": True,
            "no_private_strings_in_output": True,
            "diagnostics": details,
        }
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(PASS)


if __name__ == "__main__":
    main()
