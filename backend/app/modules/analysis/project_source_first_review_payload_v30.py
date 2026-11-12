"""Pure v30 Source-First review-payload assembler, no persistence.

Preserve all non-project AI candidate sections from the existing run. Replace
ONLY project candidates with the verified native PDF project groups and their
literal source-cell roles. Never infer taxonomy codes or auto-merge name variants.

Returns existing Analysis Candidate and DiffSpec data compatible with the
AnalysisDetailPage's shape (without live IDs, review decisions or Evidence).
All verification against a current profile revision is read-only and is NOT a
new runnable AnalysisRun.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.diff_engine import DiffSpec, build_diffs
from app.modules.analysis.project_source_first_bridge_v30 import make_candidate_bridge
from app.modules.analysis.project_source_first_candidate_v30 import build_source_first_candidates

PROJECT_PATH = re.compile(r"^projects\[(\d+)\](?:\.|$)")


@dataclass
class ReviewPayloadPreview:
    candidate: ProfileCandidateDocument
    diffs: list[DiffSpec]
    diagnostics: dict[str, Any]


def assemble_review_payload(
    *,
    source_rows: dict[str, Any],
    ai_fixture: dict[str, Any],
    private_preview: dict[str, Any],
    original_candidate_json: dict[str, Any],
    current_profile_snapshot: dict[str, Any],
) -> ReviewPayloadPreview:
    """Fail closed when any of the 36 original source-group projects vanishes."""
    _, expected = build_source_first_candidates(source_rows, ai_fixture)
    if expected != private_preview:
        raise ValueError("V30_REVIEW_PREVIEW_FINGERPRINT_MISMATCH")

    if not isinstance(original_candidate_json, dict):
        raise ValueError("V30_REVIEW_ORIGINAL_CANDIDATE_INVALID")
    if (
        not isinstance(current_profile_snapshot, dict)
        or not isinstance(current_profile_snapshot.get("projects"), list)
    ):
        raise ValueError("V30_REVIEW_CURRENT_SNAPSHOT_INVALID")

    # Existing v25 CORE is authoritative for profile, employment, skills,
    # education etc. Replacing projects must NEVER erase any CORE fields.
    try:
        previous = ProfileCandidateDocument.model_validate(original_candidate_json)
    except ValidationError:
        raise ValueError("V30_REVIEW_ORIGINAL_CANDIDATE_SCHEMA_INVALID") from None
    bridge = make_candidate_bridge(
        private_preview, ai_fixture, preserve_source_roles=True
    )
    source_projects = bridge.candidate.projects
    if not source_projects:
        raise ValueError("V30_REVIEW_SOURCE_PROJECTS_EMPTY")
    proposed = previous.model_copy(update={"projects": source_projects})

    old_dump = previous.model_dump(mode="json")
    new_dump = proposed.model_dump(mode="json")
    if (
        old_dump.keys() != new_dump.keys()
        or any(old_dump[key] != new_dump[key] for key in old_dump if key != "projects")
        or len(new_dump["projects"]) != len(source_projects)
        or len({row["group_id"] for row in private_preview["projects"]}) != len(source_projects)
    ):
        raise ValueError("V30_REVIEW_CORE_OR_SOURCE_PROJECTS_MODIFIED")

    # The active v25 quality guard can suppress a synthetic name/customer row,
    # so refuse the preview rather than allow silent loss of native PDF rows.
    guarded = apply_candidate_quality_guards(
        proposed, confirmed_person_name=None,
        page_texts=None, document_type_by_id=None,
        strict_section_authority=False,
    )
    guarded_keys = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in guarded.projects
    ]
    source_keys = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in proposed.projects
    ]
    if guarded_keys != source_keys:
        raise ValueError("V30_REVIEW_SOURCE_PROJECTS_DROPPED_BY_QUALITY_GUARD")

    specs = build_diffs(proposed, current_profile_snapshot)
    project_indices = [
        int(match.group(1))
        for spec in specs
        if spec.entity_type == "PROJECT"
        if (match := PROJECT_PATH.match(spec.candidate_path or ""))
    ]
    expected_indices = set(range(len(source_projects)))
    if set(project_indices) != expected_indices:
        raise ValueError("V30_REVIEW_DIFF_DROPPED_SOURCE_PROJECT")
    if not specs:
        raise ValueError("V30_REVIEW_EMPTY_DIFF")

    role_texts = [
        p.responsibilities
        for p in source_projects if isinstance(p.responsibilities, str)
    ]
    diagnostics = {
        "mode": "v30_source_first_review_payload_local_preview",
        "project_source_groups": len(source_projects),
        "project_candidate_count": len(proposed.projects),
        "project_indices_represented_in_diff": len(set(project_indices)),
        "project_diff_count": len(project_indices),
        "diff_total_count": len(specs),
        "diff_type_counts": dict(Counter(s.change_type for s in specs)),
        "project_diff_type_counts": dict(Counter(
            s.change_type for s in specs if s.entity_type == "PROJECT"
        )),
        "source_role_text_preserved_groups": len(role_texts),
        "source_only_project_count": sum(
            not p["enrichment_suggestions"] for p in private_preview["projects"]
        ),
        "name_variant_pairs_kept_separate": len(
            private_preview["name_variant_pairs_remain_independent"]
        ),
        "root_original_quote_ref_groups": bridge.diagnostics[
            "root_original_quote_ref_groups"
        ],
        "unlinked_ai_suggestion_count": bridge.diagnostics["unlinked_ai_count"],
        "original_non_project_sections_preserved": True,
        "existing_quality_guard_keeps_all_project_identities": True,
        "all_projects_represented_by_existing_diff": True,
        "no_automatic_source_group_merge": True,
        "no_automatic_ai_relation_code_mapping": True,
        "no_db_writes_or_llm_calls": True,
    }
    return ReviewPayloadPreview(
        candidate=proposed,
        diffs=specs,
        diagnostics=diagnostics,
    )
