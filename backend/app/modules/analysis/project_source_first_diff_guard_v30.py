"""Opt-in v30-only Diff safety: never auto-link separate native PDF groups.

The legacy Diff engine accepts nearly identical project titles with the
same customer and dates as one existing project. That is appropriate for the
normal v25 path, but violates v30's Source-First rule: distinct native groups
may be registered independently, and title variants must not update another
project's record without explicit post-registration review.

This wrapper is only used by v30 pilot builders. It leaves ALL non-project
Diffs and the existing engine unchanged. Unsafe project links are replaced
by a standalone PROJECT root REVIEW (ACCEPTED means create *new* in Confirm).
There are no writes or review decisions.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.modules.analysis.diff_engine import (
    DiffSpec, _project_date_precision_compatible, build_diffs,
)

_PATH = re.compile(r"^projects\[(\d+)\](?:\.|$)")


@dataclass
class SourceFirstDiff:
    specs: list[DiffSpec]
    variant_review_indices: list[int]
    collision_review_indices: list[int]


def _exact_identity(project: Any, target: dict[str, Any]) -> bool:
    """Require exact native title/customer and compatible period to auto-link."""
    if not isinstance(target, dict):
        return False
    name = project.project_name
    customer = project.customer_name
    # Never allow generic/substring/Levenshtein name matching here.
    if not isinstance(name, str) or not isinstance(customer, str):
        return False
    if (
        name.strip() != str(target.get("project_name") or "").strip()
        or customer.strip() != str(target.get("customer_name") or "").strip()
    ):
        return False
    return (
        _project_date_precision_compatible(
            candidate=project.start_date, existing=target.get("start_date")
        )
        and _project_date_precision_compatible(
            candidate=project.end_date, existing=target.get("end_date")
        )
    )


def build_source_first_review_safe_diffs(
    candidate: ProfileCandidateDocument,
    latest_snapshot: dict[str, Any],
) -> SourceFirstDiff:
    if not isinstance(latest_snapshot, dict) or not isinstance(
        latest_snapshot.get("projects"), list
    ):
        raise ValueError("V30_SAFE_DIFF_SNAPSHOT_INVALID")
    raw = build_diffs(candidate, latest_snapshot)
    by_id = {}
    for row in latest_snapshot["projects"]:
        if not isinstance(row, dict):
            raise ValueError("V30_SAFE_DIFF_EXISTING_PROJECT_INVALID")
        ident = row.get("id")
        if ident is None:
            continue
        key = str(ident)
        if key in by_id:
            raise ValueError("V30_SAFE_DIFF_DUPLICATE_CONFIRMED_ID")
        by_id[key] = row

    by_index: dict[int, list[DiffSpec]] = defaultdict(list)
    for spec in raw:
        if spec.entity_type != "PROJECT":
            continue
        found = _PATH.match(spec.candidate_path or "")
        if found:
            by_index[int(found.group(1))].append(spec)

    expected = set(range(len(candidate.projects)))
    if set(by_index) != expected:
        raise ValueError("V30_SAFE_DIFF_SOURCE_PROJECT_UNREPRESENTED")

    variant: set[int] = set()
    linked: dict[int, str] = {}
    for index, project in enumerate(candidate.projects):
        specs = by_index[index]
        targets = {str(s.existing_target_id)
                   for s in specs if s.existing_target_id is not None}
        non_root = any(s.field_name is not None or
                       (s.candidate_path or "") != f"projects[{index}]"
                       for s in specs)
        # NEW root may legitimately emit child relation REVIEW items with
        # no existing target. Only an UPDATE-like field without a target is
        # invalid; it would be impossible to apply safely in Confirm.
        new_root = any(
            s.candidate_path == f"projects[{index}]"
            and s.field_name is None
            and s.change_type in {"NEW", "REVIEW"}
            for s in specs
        )
        if non_root and not targets and not new_root:
            raise ValueError(f"V30_SAFE_DIFF_FIELD_WITHOUT_TARGET:{index}")
        if len(targets) > 1:
            variant.add(index)
            continue
        if not targets:
            continue
        target_id = next(iter(targets))
        old = by_id.get(target_id)
        if old is None:
            raise ValueError(f"V30_SAFE_DIFF_TARGET_MISSING:{index}")
        if not _exact_identity(project, old):
            variant.add(index)
        elif non_root or any(s.change_type == "SAME" for s in specs):
            linked[index] = target_id

    # Detect multiple native project groups aiming to UPDATE the same record.
    # Exclude name variants: these are already forced to root REVIEW.
    targets_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, target in linked.items():
        if idx not in variant:
            targets_to_indices[target].append(idx)
    collision = {
        idx for group in targets_to_indices.values() if len(group) > 1
        for idx in group
    }
    unsafe = variant | collision
    if not unsafe:
        return SourceFirstDiff(raw, [], [])

    result: list[DiffSpec] = []
    rewritten: set[int] = set()
    for spec in raw:
        found = _PATH.match(spec.candidate_path or "") if spec.entity_type == "PROJECT" else None
        if found is None:
            result.append(spec)
            continue
        idx = int(found.group(1))
        if idx not in unsafe:
            result.append(spec)
            continue
        if idx in rewritten:
            continue
        rewritten.add(idx)
        source = candidate.projects[idx]
        result.append(DiffSpec(
            entity_type="PROJECT",
            candidate_path=f"projects[{idx}]",
            field_name=None,
            change_type="REVIEW",
            existing_target_id=None,  # Never auto-update another project.
            new_value=source.model_dump(mode="json"),
            confidence=source.confidence,
            source_refs=[x.model_dump(mode="json") for x in source.source_refs],
        ))
    if rewritten != unsafe:
        raise ValueError("V30_SAFE_DIFF_REWRITE_INCOMPLETE")
    return SourceFirstDiff(
        specs=result,
        variant_review_indices=sorted(variant),
        collision_review_indices=sorted(collision),
    )
