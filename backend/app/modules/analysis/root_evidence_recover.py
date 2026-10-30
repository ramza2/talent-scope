"""Recover verified ROOT JOB/EXP source_refs on REVIEWING analyses.

Memory-only date reconciliation (for ownership verification) + project-relation
root evidence supplement. Persist only ``source_refs`` on ROOT JOB/EXP items
that have exactly one matching PENDING Diff (path+code[+job_type]). Never
regenerates Diff rows, never mutates stored project dates, raw_value, code,
confidence, review_status, or decided_value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.schemas.profile_candidate import (
    ExpertiseCandidate,
    JobCandidate,
    ProfileCandidateDocument,
    SourceRef,
)
from app.core.exceptions import (
    AnalysisStateConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.person import PersonProfile
from app.modules.analysis.compact_v8 import (
    backfill_project_dates_from_root_evidence,
    supplement_root_job_exp_evidence_from_project_relations,
)
from app.modules.analysis.guard_refresh import load_page_context, parse_stored_candidate
from app.modules.analysis.repository import AnalysisRepository


def _refs_nonempty(refs: list[SourceRef] | None) -> bool:
    return any((ref.quote_text or "").strip() for ref in (refs or []))


def _refs_dump(refs: list[SourceRef] | None) -> list[dict[str, Any]]:
    return [
        {
            "document_id": ref.document_id,
            "page_no": ref.page_no,
            "quote_text": ref.quote_text,
        }
        for ref in (refs or [])
    ]


def _allowed_documents_from_pages(
    page_texts: dict[tuple[str, int], str],
) -> dict[str, set[int]]:
    allowed: dict[str, set[int]] = {}
    for doc_id, page_no in page_texts:
        allowed.setdefault(doc_id, set()).add(page_no)
    return allowed


def _norm_job_type(value: Any) -> str:
    text = (str(value).strip() if value is not None else "") or "PRIMARY"
    return text


@dataclass(frozen=True)
class _EligibleRootSlot:
    entity_type: str  # JOB | EXP
    index: int
    code: str
    job_type: str | None
    path: str
    diff: AnalysisDiffItem
    source_refs: list[SourceRef]


@dataclass(frozen=True)
class RootEvidenceRecoverComputation:
    before_candidate: ProfileCandidateDocument
    after_candidate: ProfileCandidateDocument
    before_storage: dict[str, Any]
    after_storage: dict[str, Any]
    supplemented_jobs: list[str] = field(default_factory=list)
    supplemented_expertise: list[str] = field(default_factory=list)
    eligible_slots: list[_EligibleRootSlot] = field(default_factory=list)
    pending_diffs_updated: int = 0
    changed: bool = False


@dataclass(frozen=True)
class RootEvidenceRecoverResult:
    analysis_id: UUID
    status: str
    changed: bool
    applied: bool
    supplemented_jobs: list[str]
    supplemented_expertise: list[str]
    pending_diffs_updated: int
    project_dates_unchanged: bool
    message: str | None = None


def assert_root_evidence_recover_eligible(
    repo: AnalysisRepository,
    run: AnalysisRun,
    profile: PersonProfile | None,
) -> None:
    """REVIEWING + base version + documents. Reviewed diffs are allowed."""
    if run.status in {"CONFIRMED", "CANCELLED"}:
        raise AnalysisStateConflictError(
            f"{run.status} 상태의 분석에는 ROOT 근거 복구를 할 수 없습니다."
        )
    if run.status != "REVIEWING":
        raise AnalysisStateConflictError(
            "REVIEWING 상태의 분석만 ROOT 근거 복구를 할 수 있습니다."
        )
    if run.base_profile_version is None:
        raise AnalysisStateConflictError(
            "기준 프로필 버전이 없어 ROOT 근거 복구를 할 수 없습니다."
        )
    if profile is None:
        raise NotFoundError("프로필을 찾을 수 없습니다.")
    if int(profile.profile_version) != int(run.base_profile_version):
        raise AnalysisStateConflictError(
            "프로필 버전이 변경되어 ROOT 근거 복구를 할 수 없습니다."
        )
    load_page_context(repo, run)


def _index_root_diffs(
    diffs: list[AnalysisDiffItem],
) -> dict[str, list[AnalysisDiffItem]]:
    """Map candidate_path → JOB/EXP diffs. Fail-closed on duplicate paths."""
    by_path: dict[str, list[AnalysisDiffItem]] = {}
    for diff in diffs:
        if diff.entity_type not in {"JOB", "EXP"}:
            continue
        path = (diff.candidate_path or "").strip()
        if not path:
            raise ValidationAppError(
                "ROOT JOB/EXP Diff에 candidate_path가 없어 근거 복구를 중단합니다."
            )
        by_path.setdefault(path, []).append(diff)
    for path, items in by_path.items():
        if len(items) > 1:
            raise ValidationAppError(
                f"ROOT Diff 경로가 중복되어 근거 복구를 중단합니다: {path}"
            )
    return by_path


def _pending_diff_for_slot(
    *,
    by_path: dict[str, list[AnalysisDiffItem]],
    entity_type: str,
    index: int,
    code: str,
    job_type: str | None,
) -> AnalysisDiffItem | None:
    """Return the unique PENDING Diff for this ROOT slot, or None if ineligible.

    Reviewed (non-PENDING) slots and missing Diffs are skipped. Path/code/
    job_type mismatches fail closed.
    """
    path = f"jobs[{index}]" if entity_type == "JOB" else f"expertise[{index}]"
    items = by_path.get(path)
    if not items:
        return None
    diff = items[0]
    if diff.entity_type != entity_type:
        raise ValidationAppError(
            f"ROOT Diff 유형이 경로와 불일치합니다: {path}"
        )
    if diff.review_status != "PENDING":
        # Already reviewed — never mutate candidate or Diff.
        return None
    nv = diff.new_value
    if not isinstance(nv, dict):
        raise ValidationAppError(
            f"PENDING ROOT Diff new_value가 객체가 아닙니다: {path}"
        )
    nv_code = (nv.get("code") or "").strip()
    if nv_code != code:
        raise ValidationAppError(
            f"PENDING ROOT Diff code가 후보와 불일치합니다: {path}"
        )
    if entity_type == "JOB":
        nv_type = _norm_job_type(nv.get("job_type"))
        cand_type = _norm_job_type(job_type)
        if nv_type != cand_type:
            raise ValidationAppError(
                f"PENDING ROOT Diff job_type이 후보와 불일치합니다: {path}"
            )
    return diff


def _merge_eligible_source_refs_only(
    original: ProfileCandidateDocument,
    supplemented: ProfileCandidateDocument,
    *,
    eligible_job_indexes: set[int],
    eligible_exp_indexes: set[int],
) -> ProfileCandidateDocument:
    """Copy only ``source_refs`` for eligible indexes; preserve all other fields."""
    before_jobs = list(original.jobs or [])
    after_jobs = list(supplemented.jobs or [])
    if len(before_jobs) != len(after_jobs):
        raise ValidationAppError("ROOT JOB 목록 길이가 복구 계산 중 변경되었습니다.")
    new_jobs: list[JobCandidate] = []
    for idx, (before, after) in enumerate(
        zip(before_jobs, after_jobs, strict=True)
    ):
        if (before.code or "").strip() != (after.code or "").strip():
            raise ValidationAppError("ROOT JOB 코드 순서가 복구 계산 중 변경되었습니다.")
        if (
            idx in eligible_job_indexes
            and not _refs_nonempty(before.source_refs)
            and _refs_nonempty(after.source_refs)
        ):
            new_jobs.append(
                before.model_copy(update={"source_refs": after.source_refs})
            )
        else:
            new_jobs.append(before)

    before_exps = list(original.expertise or [])
    after_exps = list(supplemented.expertise or [])
    if len(before_exps) != len(after_exps):
        raise ValidationAppError("ROOT EXP 목록 길이가 복구 계산 중 변경되었습니다.")
    new_exps: list[ExpertiseCandidate] = []
    for idx, (before, after) in enumerate(
        zip(before_exps, after_exps, strict=True)
    ):
        if (before.code or "").strip() != (after.code or "").strip():
            raise ValidationAppError("ROOT EXP 코드 순서가 복구 계산 중 변경되었습니다.")
        if (
            idx in eligible_exp_indexes
            and not _refs_nonempty(before.source_refs)
            and _refs_nonempty(after.source_refs)
        ):
            new_exps.append(
                before.model_copy(update={"source_refs": after.source_refs})
            )
        else:
            new_exps.append(before)

    return original.model_copy(update={"jobs": new_jobs, "expertise": new_exps})


def _build_eligible_slots(
    before: ProfileCandidateDocument,
    supplemented: ProfileCandidateDocument,
    diffs: list[AnalysisDiffItem],
) -> list[_EligibleRootSlot]:
    by_path = _index_root_diffs(diffs)
    slots: list[_EligibleRootSlot] = []

    for idx, (before_job, after_job) in enumerate(
        zip(before.jobs or [], supplemented.jobs or [], strict=True)
    ):
        code = (before_job.code or "").strip()
        if not code:
            continue
        if _refs_nonempty(before_job.source_refs):
            continue
        if not _refs_nonempty(after_job.source_refs):
            continue
        job_type = _norm_job_type(before_job.job_type)
        diff = _pending_diff_for_slot(
            by_path=by_path,
            entity_type="JOB",
            index=idx,
            code=code,
            job_type=job_type,
        )
        if diff is None:
            continue
        slots.append(
            _EligibleRootSlot(
                entity_type="JOB",
                index=idx,
                code=code,
                job_type=job_type,
                path=f"jobs[{idx}]",
                diff=diff,
                source_refs=list(after_job.source_refs or []),
            )
        )

    for idx, (before_exp, after_exp) in enumerate(
        zip(before.expertise or [], supplemented.expertise or [], strict=True)
    ):
        code = (before_exp.code or "").strip()
        if not code:
            continue
        if _refs_nonempty(before_exp.source_refs):
            continue
        if not _refs_nonempty(after_exp.source_refs):
            continue
        diff = _pending_diff_for_slot(
            by_path=by_path,
            entity_type="EXP",
            index=idx,
            code=code,
            job_type=None,
        )
        if diff is None:
            continue
        slots.append(
            _EligibleRootSlot(
                entity_type="EXP",
                index=idx,
                code=code,
                job_type=None,
                path=f"expertise[{idx}]",
                diff=diff,
                source_refs=list(after_exp.source_refs or []),
            )
        )
    return slots


def compute_root_evidence_recover(
    repo: AnalysisRepository,
    run: AnalysisRun,
) -> RootEvidenceRecoverComputation:
    before = parse_stored_candidate(run.candidate_json)
    before_storage = before.to_storage_dict()
    page_texts, _doc_types = load_page_context(repo, run)
    allowed = _allowed_documents_from_pages(page_texts)
    diffs = repo.list_diffs(run.id)

    # Date repair is verification-only memory; never persisted as project dates.
    dated = backfill_project_dates_from_root_evidence(
        before,
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    supplemented, _report = supplement_root_job_exp_evidence_from_project_relations(
        dated,
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    if len(before.jobs or []) != len(supplemented.jobs or []):
        raise ValidationAppError("ROOT JOB 목록 길이가 복구 계산 중 변경되었습니다.")
    if len(before.expertise or []) != len(supplemented.expertise or []):
        raise ValidationAppError("ROOT EXP 목록 길이가 복구 계산 중 변경되었습니다.")

    slots = _build_eligible_slots(before, supplemented, diffs)
    eligible_job_indexes = {s.index for s in slots if s.entity_type == "JOB"}
    eligible_exp_indexes = {s.index for s in slots if s.entity_type == "EXP"}
    after = _merge_eligible_source_refs_only(
        before,
        supplemented,
        eligible_job_indexes=eligible_job_indexes,
        eligible_exp_indexes=eligible_exp_indexes,
    )
    after_storage = after.to_storage_dict()

    # Hard safety: projects / non-root entities must be byte-identical in storage.
    if after_storage.get("projects") != before_storage.get("projects"):
        raise ValidationAppError(
            "ROOT 근거 복구가 프로젝트 데이터를 변경하려 해 중단되었습니다."
        )
    for key in (
        "education",
        "certifications",
        "employment_history",
        "skills",
        "profile",
        "summary",
    ):
        if after_storage.get(key) != before_storage.get(key):
            raise ValidationAppError(
                f"ROOT 근거 복구가 {key} 데이터를 변경하려 해 중단되었습니다."
            )

    # Only source_refs may differ inside jobs/expertise entries.
    for before_job, after_job in zip(
        before_storage.get("jobs") or [],
        after_storage.get("jobs") or [],
        strict=True,
    ):
        b = dict(before_job)
        a = dict(after_job)
        b.pop("source_refs", None)
        a.pop("source_refs", None)
        if a != b:
            raise ValidationAppError(
                "ROOT JOB에서 source_refs 외 필드 변경이 감지되어 중단되었습니다."
            )
    for before_exp, after_exp in zip(
        before_storage.get("expertise") or [],
        after_storage.get("expertise") or [],
        strict=True,
    ):
        b = dict(before_exp)
        a = dict(after_exp)
        b.pop("source_refs", None)
        a.pop("source_refs", None)
        if a != b:
            raise ValidationAppError(
                "ROOT EXP에서 source_refs 외 필드 변경이 감지되어 중단되었습니다."
            )

    changed = after_storage != before_storage
    return RootEvidenceRecoverComputation(
        before_candidate=before,
        after_candidate=after,
        before_storage=before_storage,
        after_storage=after_storage,
        supplemented_jobs=[s.code for s in slots if s.entity_type == "JOB"],
        supplemented_expertise=[s.code for s in slots if s.entity_type == "EXP"],
        eligible_slots=slots,
        pending_diffs_updated=len(slots),
        changed=changed,
    )


def _patch_pending_root_diffs(slots: list[_EligibleRootSlot]) -> int:
    """Write source_refs into eligible PENDING Diff new_value only."""
    updated = 0
    for slot in slots:
        diff = slot.diff
        if diff.review_status != "PENDING":
            raise ValidationAppError(
                f"검토 완료 Diff는 변경할 수 없습니다: {slot.path}"
            )
        nv = diff.new_value
        if not isinstance(nv, dict):
            raise ValidationAppError(
                f"PENDING ROOT Diff new_value가 객체가 아닙니다: {slot.path}"
            )
        # Re-validate path/code/job_type immediately before write.
        if (diff.candidate_path or "").strip() != slot.path:
            raise ValidationAppError(
                f"Diff candidate_path 불일치로 중단합니다: {slot.path}"
            )
        if (nv.get("code") or "").strip() != slot.code:
            raise ValidationAppError(
                f"Diff code 불일치로 중단합니다: {slot.path}"
            )
        if slot.entity_type == "JOB":
            if _norm_job_type(nv.get("job_type")) != _norm_job_type(slot.job_type):
                raise ValidationAppError(
                    f"Diff job_type 불일치로 중단합니다: {slot.path}"
                )
        patched = dict(nv)
        patched["source_refs"] = _refs_dump(slot.source_refs)
        # raw_value / code / confidence / decided_value / review_status untouched.
        if patched != nv:
            diff.new_value = patched
            updated += 1
    return updated


def preview_root_evidence_recover(
    db: Session, analysis_id: UUID
) -> RootEvidenceRecoverResult:
    """Read-only preview — no DB writes."""
    repo = AnalysisRepository(db)
    run = repo.get_run(analysis_id)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")
    profile = repo.get_profile(run.person_id)
    assert_root_evidence_recover_eligible(repo, run, profile)
    computation = compute_root_evidence_recover(repo, run)
    message = None
    if not computation.changed:
        message = "복구할 ROOT JOB/EXP 근거가 없습니다."
    return RootEvidenceRecoverResult(
        analysis_id=run.id,
        status=run.status,
        changed=computation.changed,
        applied=False,
        supplemented_jobs=computation.supplemented_jobs,
        supplemented_expertise=computation.supplemented_expertise,
        pending_diffs_updated=computation.pending_diffs_updated,
        project_dates_unchanged=True,
        message=message,
    )


def apply_root_evidence_recover(
    db: Session,
    analysis_id: UUID,
    *,
    actor_user_id: UUID,
) -> RootEvidenceRecoverResult:
    """Persist ROOT JOB/EXP evidence under AnalysisRun→Person→Profile locks."""
    repo = AnalysisRepository(db)
    pending_updated = 0
    try:
        run = repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")

        person = repo.get_person(run.person_id, for_update=True)
        if (
            person is None
            or person.deleted_at is not None
            or person.status == "DELETED"
        ):
            raise NotFoundError("인력을 찾을 수 없습니다.")

        profile = repo.get_profile(run.person_id, for_update=True)
        assert_root_evidence_recover_eligible(repo, run, profile)
        computation = compute_root_evidence_recover(repo, run)

        if not computation.changed:
            db.commit()
            return RootEvidenceRecoverResult(
                analysis_id=run.id,
                status=run.status,
                changed=False,
                applied=False,
                supplemented_jobs=[],
                supplemented_expertise=[],
                pending_diffs_updated=0,
                project_dates_unchanged=True,
                message="복구할 ROOT JOB/EXP 근거가 없습니다.",
            )

        pending_updated = _patch_pending_root_diffs(computation.eligible_slots)

        run.candidate_json = computation.after_storage
        run.updated_at = datetime.now(UTC)
        db.add(run)
        db.flush()

        repo.add_audit(
            action_type="ANALYSIS_ROOT_EVIDENCE_RECOVER",
            actor_user_id=actor_user_id,
            target_id=run.id,
            before={
                "status": "REVIEWING",
                "supplemented_jobs": [],
                "supplemented_expertise": [],
            },
            after={
                "status": "REVIEWING",
                "supplemented_jobs": computation.supplemented_jobs,
                "supplemented_expertise": computation.supplemented_expertise,
                "pending_diffs_updated": pending_updated,
                "project_dates_unchanged": True,
            },
            metadata={
                "operation": "root_evidence_recover",
                "changed": True,
                "base_profile_version": run.base_profile_version,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return RootEvidenceRecoverResult(
        analysis_id=run.id,
        status=run.status,
        changed=True,
        applied=True,
        supplemented_jobs=computation.supplemented_jobs,
        supplemented_expertise=computation.supplemented_expertise,
        pending_diffs_updated=pending_updated,
        project_dates_unchanged=True,
        message="ROOT JOB/EXP 근거를 복구했습니다.",
    )
