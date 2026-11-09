"""Conservatively refresh previously saved employment NEW -> REVIEW decisions.

Only the classification and confirmed-match metadata of untouched PENDING root
diffs may change. Candidate data, diff identities, evidence links, and every
other review decision remain intact.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    AnalysisStateConflictError,
    NotFoundError,
)
from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.person import PersonProfile
from app.modules.analysis.diff_engine import DiffSpec, build_diffs
from app.modules.analysis.guard_refresh import parse_stored_candidate
from app.modules.analysis.repository import AnalysisRepository

_EMPLOYMENT_ROOT = re.compile(r"^employment_history\[\d+\]$")


@dataclass(frozen=True)
class EmploymentDiffRefreshPlan:
    before_counts: dict[str, int]
    after_counts: dict[str, int]
    changes: tuple[tuple[AnalysisDiffItem, DiffSpec], ...]


@dataclass(frozen=True)
class EmploymentDiffRefreshResult:
    analysis_id: UUID
    changed_paths: tuple[str, ...]
    before_counts: dict[str, int]
    after_counts: dict[str, int]
    applied: bool


def _eligible_snapshot(
    repo: AnalysisRepository,
    run: AnalysisRun,
    profile: PersonProfile | None,
) -> dict:
    if run.status != "REVIEWING":
        raise AnalysisStateConflictError("REVIEWING 분석만 Diff를 갱신할 수 있습니다.")
    if (
        profile is None
        or run.base_profile_version is None
        or profile.profile_version != run.base_profile_version
    ):
        raise AnalysisStateConflictError("기준 프로필 버전이 변경되었습니다.")
    snapshot = repo.get_profile_revision_snapshot(
        run.person_id, run.base_profile_version
    )
    if snapshot is None:
        raise AnalysisStateConflictError("기준 프로필 스냅샷이 없습니다.")
    return snapshot


def plan_employment_diff_refresh(
    saved: list[AnalysisDiffItem],
    recomputed: list[DiffSpec],
) -> EmploymentDiffRefreshPlan:
    """Reject any unexpected drift; never replace/recreate persisted diffs."""
    saved_by_path = {item.candidate_path: item for item in saved}
    recomputed_by_path = {item.candidate_path: item for item in recomputed}
    if (
        len(saved_by_path) != len(saved)
        or len(recomputed_by_path) != len(recomputed)
        or set(saved_by_path) != set(recomputed_by_path)
        or any(not path or not _EMPLOYMENT_ROOT.fullmatch(path) for path in saved_by_path)
    ):
        raise AnalysisStateConflictError(
            "재직 이력 Diff 구조가 달라 안전한 부분 갱신이 불가능합니다."
        )

    changes: list[tuple[AnalysisDiffItem, DiffSpec]] = []
    for path, row in saved_by_path.items():
        target = recomputed_by_path[path]
        if row.change_type == target.change_type:
            continue
        if row.change_type != "NEW" or target.change_type != "REVIEW":
            raise AnalysisStateConflictError(
                "NEW→REVIEW 외의 판정 변경이 있어 갱신을 중단했습니다."
            )
        if row.review_status != "PENDING" or row.field_name is not None:
            raise AnalysisStateConflictError(
                "이미 검토된 재직 이력은 자동 재분류하지 않습니다."
            )
        if (
            row.old_value is not None
            or row.existing_target_id is not None
            or row.new_value != target.to_persist_dict()["new_value"]
        ):
            raise AnalysisStateConflictError(
                "저장된 후보 Diff가 현재 분석 후보와 일치하지 않습니다."
            )
        changes.append((row, target))

    return EmploymentDiffRefreshPlan(
        before_counts=dict(Counter(item.change_type for item in saved)),
        after_counts=dict(Counter(item.change_type for item in recomputed)),
        changes=tuple(changes),
    )


def _compute(
    repo: AnalysisRepository,
    run: AnalysisRun,
    snapshot: dict,
) -> EmploymentDiffRefreshPlan:
    candidate = parse_stored_candidate(run.candidate_json)
    fresh = [
        spec for spec in build_diffs(candidate, snapshot)
        if spec.entity_type == "EMPLOYMENT"
    ]
    saved = repo.list_diffs(run.id, entity_type="EMPLOYMENT")
    return plan_employment_diff_refresh(saved, fresh)


def _result(
    run: AnalysisRun,
    plan: EmploymentDiffRefreshPlan,
    *,
    applied: bool,
) -> EmploymentDiffRefreshResult:
    return EmploymentDiffRefreshResult(
        analysis_id=run.id,
        changed_paths=tuple(row.candidate_path for row, _ in plan.changes),
        before_counts=plan.before_counts,
        after_counts=plan.after_counts,
        applied=applied,
    )


def preview_employment_diff_refresh(
    db: Session, analysis_id: UUID
) -> EmploymentDiffRefreshResult:
    repo = AnalysisRepository(db)
    run = repo.get_run(analysis_id)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")
    snapshot = _eligible_snapshot(repo, run, repo.get_profile(run.person_id))
    return _result(run, _compute(repo, run, snapshot), applied=False)


def apply_employment_diff_refresh(
    db: Session, analysis_id: UUID, *, actor_user_id: UUID
) -> EmploymentDiffRefreshResult:
    repo = AnalysisRepository(db)
    try:
        # Same lock order as Confirm: Run -> Person -> Profile.
        run = repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        person = repo.get_person(run.person_id, for_update=True)
        if person is None or person.deleted_at is not None or person.status == "DELETED":
            raise NotFoundError("인력을 찾을 수 없습니다.")
        profile = repo.get_profile(run.person_id, for_update=True)
        snapshot = _eligible_snapshot(repo, run, profile)
        plan = _compute(repo, run, snapshot)

        if not plan.changes:
            result = _result(run, plan, applied=False)
            db.rollback()
            return result

        for row, spec in plan.changes:
            row.change_type = "REVIEW"
            row.old_value = spec.old_value
            row.existing_target_id = spec.existing_target_id
            db.add(row)
        run.updated_at = datetime.now(UTC)
        db.add(run)
        repo.add_audit(
            action_type="ANALYSIS_EMPLOYMENT_DIFF_REFRESH",
            actor_user_id=actor_user_id,
            target_id=run.id,
            before={"employment_diff_counts": plan.before_counts},
            after={"employment_diff_counts": plan.after_counts},
            metadata={
                "operation": "employment_diff_refresh",
                "base_profile_version": run.base_profile_version,
                "changed_paths": [row.candidate_path for row, _ in plan.changes],
                "preserved_diff_ids": True,
            },
        )
        db.commit()
        return _result(run, plan, applied=True)
    except Exception:
        db.rollback()
        raise
