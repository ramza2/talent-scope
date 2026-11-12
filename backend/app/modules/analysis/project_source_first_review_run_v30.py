"""Explicitly opted-in creation of an isolated v30 REVIEWING AnalysisRun.

Never invoked by the normal v25 AnalysisService, worker, UI or scheduler.
Uses already computed *direct native-PDF* Source-First candidate + safe Diff,
checks source run, documents, person/profile revision and original fingerprint
again inside ONE transaction. Returns one new REVIEWING run for existing 6-tab
review UI. No automatic approval/confirmation, VLM/LLM calls, or profile writes.

Caller must explicitly opt in. It is unsafe to reuse a dry-run result after
the person's profile version changes; this implementation rejects that case.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.db.models.analysis import AnalysisRun
from app.modules.analysis.diff_engine import DiffSpec
from app.modules.analysis.project_source_first_diff_guard_v30 import (
    build_source_first_review_safe_diffs,
)
from app.modules.analysis.repository import AnalysisRepository


V30_PROMPT_LABEL = "profile-source-first-v30-native-direct"
V30_AUDIT_ACTION = "ANALYSIS_V30_SOURCE_FIRST_CREATE"


def fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class V30RunPlan:
    original_run_id: UUID
    original_candidate_sha: str
    person_id: UUID
    document_ids: tuple[UUID, ...]
    base_profile_version: int
    latest_snapshot_sha: str
    candidate: ProfileCandidateDocument
    diff_specs: tuple[DiffSpec, ...]
    source_group_count: int
    represented_group_count: int
    role_group_count: int
    blocked_variant_links: int
    blocked_collision_links: int


def create_plan(
    *,
    original_run_id: UUID,
    original_candidate_sha: str,
    person_id: UUID,
    document_ids: tuple[UUID, ...],
    base_profile_version: int,
    latest_snapshot: dict[str, Any],
    candidate: ProfileCandidateDocument,
    diff_specs: list[DiffSpec],
    source_group_count: int,
    role_group_count: int,
    blocked_variant_links: int,
    blocked_collision_links: int,
) -> V30RunPlan:
    if (
        not isinstance(original_run_id, UUID)
        or not isinstance(person_id, UUID)
        or type(base_profile_version) is not int or base_profile_version < 1
        or not isinstance(original_candidate_sha, str)
        or len(original_candidate_sha) != 64
        or any(c not in "0123456789abcdef" for c in original_candidate_sha)
        or not isinstance(latest_snapshot, dict)
        or not isinstance(latest_snapshot.get("projects"), list)
        or not document_ids
        or any(not isinstance(d, UUID) for d in document_ids)
        or len(document_ids) != len(set(document_ids))
        or not isinstance(candidate, ProfileCandidateDocument)
    ):
        raise ValueError("V30_RUN_PLAN_INPUT_INVALID")

    if (
        len(candidate.projects) != source_group_count
        or role_group_count != source_group_count
        or source_group_count < 1
        or not diff_specs
    ):
        raise ValueError("V30_RUN_PLAN_INCOMPLETE_SOURCE_GROUPS")

    safe = build_source_first_review_safe_diffs(candidate, latest_snapshot)
    if [s.to_persist_dict() for s in safe.specs] != [
        s.to_persist_dict() for s in diff_specs
    ]:
        raise ValueError("V30_RUN_PLAN_DIFF_NOT_SOURCE_FIRST_SAFE")
    if (
        len(safe.variant_review_indices) != blocked_variant_links
        or len(safe.collision_review_indices) != blocked_collision_links
    ):
        raise ValueError("V30_RUN_PLAN_LINK_GUARD_COUNTS_MISMATCH")

    return V30RunPlan(
        original_run_id=original_run_id,
        original_candidate_sha=original_candidate_sha,
        person_id=person_id,
        document_ids=document_ids,
        base_profile_version=base_profile_version,
        latest_snapshot_sha=fingerprint(latest_snapshot),
        candidate=candidate,
        diff_specs=tuple(diff_specs),
        source_group_count=source_group_count,
        represented_group_count=source_group_count,
        role_group_count=role_group_count,
        blocked_variant_links=blocked_variant_links,
        blocked_collision_links=blocked_collision_links,
    )


def persist_reviewing_run(
    db: Session,
    plan: V30RunPlan,
    *,
    expected_revision: int,
    expected_project_count: int,
) -> UUID:
    """Called ONLY inside caller-owned transaction; does not call commit.

    Lock person before profile, following manual create lock order. The caller
    must use a new transaction and commit only after all checks pass.
    """
    if expected_revision != plan.base_profile_version:
        raise ValueError("V30_RUN_TARGET_REVISION_NOT_ACKNOWLEDGED")
    if expected_project_count != plan.source_group_count:
        raise ValueError("V30_RUN_SOURCE_PROJECT_COUNT_NOT_ACKNOWLEDGED")

    repo = AnalysisRepository(db)
    person = repo.get_person(plan.person_id, for_update=True)
    if person is None or person.status == "DELETED" or person.deleted_at is not None:
        raise ValueError("V30_RUN_PERSON_INACTIVE")
    if repo.get_manual_create_blocking_run(plan.person_id) is not None:
        raise ValueError("V30_RUN_ACTIVE_ANALYSIS_EXISTS")

    profile = repo.get_profile(plan.person_id, for_update=True)
    if profile is None or profile.profile_version != plan.base_profile_version:
        raise ValueError("V30_RUN_PROFILE_VERSION_DRIFT")

    current_snapshot = repo.get_profile_revision_snapshot(
        plan.person_id, plan.base_profile_version
    )
    if (
        not isinstance(current_snapshot, dict)
        or fingerprint(current_snapshot) != plan.latest_snapshot_sha
    ):
        raise ValueError("V30_RUN_CONFIRMED_REVISION_DRIFT")
    source_run = repo.get_run(plan.original_run_id)
    if (
        source_run is None
        or source_run.person_id != plan.person_id
        or source_run.status != "CONFIRMED"
        or not isinstance(source_run.candidate_json, dict)
        or fingerprint(source_run.candidate_json) != plan.original_candidate_sha
    ):
        raise ValueError("V30_RUN_SOURCE_ANALYSIS_CHANGED")
    source_document_ids = repo.list_run_document_ids(plan.original_run_id)
    if (
        set(source_document_ids) != set(plan.document_ids)
        or len(source_document_ids) != len(plan.document_ids)
    ):
        raise ValueError("V30_RUN_DOCUMENT_BINDING_CHANGED")
    documents = repo.list_documents_by_ids(source_document_ids)
    if (
        len(documents) != len(source_document_ids)
        or any(group.person_id != plan.person_id
               or doc.processing_status != "READY"
               for doc, group, _ in documents)
    ):
        raise ValueError("V30_RUN_SOURCE_DOCUMENT_NOT_READY_OR_NOT_OWNED")

    # One v30 REVIEWING run per person/profile revision; repeated invocation
    # is a hard error, not a second run inserted by accident.
    prior = db.execute(
        select(AnalysisRun.id).where(
            AnalysisRun.person_id == plan.person_id,
            AnalysisRun.prompt_version == V30_PROMPT_LABEL,
            AnalysisRun.base_profile_version == plan.base_profile_version,
            AnalysisRun.status.in_(("REVIEWING", "CONFIRMED")),
        ).limit(1)
    ).scalar_one_or_none()
    if prior is not None:
        raise ValueError("V30_RUN_ALREADY_EXISTS_FOR_THIS_REVISION")

    run = repo.create_run(
        person_id=plan.person_id,
        base_profile_version=plan.base_profile_version,
        llm_model=None,
        vlm_model=None,
        prompt_version=V30_PROMPT_LABEL,
        schema_version=plan.candidate.schema_version,
        status="REVIEWING",  # never enqueue normal v25 worker
    )
    repo.add_run_documents(run.id, list(plan.document_ids))
    repo.replace_diffs(
        run.id,
        [spec.to_persist_dict() for spec in plan.diff_specs],
    )
    repo.mark_reviewing(
        run,
        candidate_json=plan.candidate.to_storage_dict(),
        overall_confidence=plan.candidate.analysis.overall_confidence,
    )
    repo.add_audit(
        action_type=V30_AUDIT_ACTION,
        actor_user_id=None,  # trusted server CLI; not impersonating an admin
        target_id=run.id,
        metadata={
            "source_analysis_run_id": str(plan.original_run_id),
            "source_candidate_sha256": plan.original_candidate_sha,
            "target_profile_version": plan.base_profile_version,
            "project_count": plan.source_group_count,
            "diff_count": len(plan.diff_specs),
            "source_first": True,
            "no_llm_vlm_calls": True,
            "title_variant_links_blocked": plan.blocked_variant_links,
            "target_collisions_blocked": plan.blocked_collision_links,
        },
        after={"status": "REVIEWING"},
    )
    db.flush()
    return run.id
