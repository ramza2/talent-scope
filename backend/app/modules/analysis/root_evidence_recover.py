"""Recover verified ROOT JOB/EXP source_refs on REVIEWING analyses.

Memory-only date reconciliation (for ownership verification) + project-relation
root evidence supplement. Persist only supplemented ROOT JOB/EXP source_refs
and matching PENDING Diff new_value evidence. Never regenerates Diff rows,
never mutates stored project dates, and never touches MODIFIED/ACCEPTED/
REJECTED/MERGED decisions or decided_value.
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


@dataclass(frozen=True)
class RootEvidenceRecoverComputation:
    before_candidate: ProfileCandidateDocument
    after_candidate: ProfileCandidateDocument
    before_storage: dict[str, Any]
    after_storage: dict[str, Any]
    supplemented_jobs: list[str] = field(default_factory=list)
    supplemented_expertise: list[str] = field(default_factory=list)
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


def _merge_supplemented_root_items(
    original: ProfileCandidateDocument,
    supplemented: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """Copy only newly grounded JOB/EXP refs onto the original candidate.

    Project rows (including dates) stay exactly as stored.
    """
    before_jobs = list(original.jobs or [])
    after_jobs = list(supplemented.jobs or [])
    if len(before_jobs) != len(after_jobs):
        raise ValidationAppError("ROOT JOB 목록 길이가 복구 계산 중 변경되었습니다.")
    new_jobs: list[JobCandidate] = []
    for before, after in zip(before_jobs, after_jobs, strict=True):
        if (before.code or "").strip() != (after.code or "").strip():
            raise ValidationAppError("ROOT JOB 코드 순서가 복구 계산 중 변경되었습니다.")
        if _refs_nonempty(before.source_refs) or not _refs_nonempty(after.source_refs):
            new_jobs.append(before)
            continue
        new_jobs.append(
            before.model_copy(
                update={
                    "source_refs": after.source_refs,
                    "raw_value": after.raw_value,
                }
            )
        )

    before_exps = list(original.expertise or [])
    after_exps = list(supplemented.expertise or [])
    if len(before_exps) != len(after_exps):
        raise ValidationAppError("ROOT EXP 목록 길이가 복구 계산 중 변경되었습니다.")
    new_exps: list[ExpertiseCandidate] = []
    for before, after in zip(before_exps, after_exps, strict=True):
        if (before.code or "").strip() != (after.code or "").strip():
            raise ValidationAppError("ROOT EXP 코드 순서가 복구 계산 중 변경되었습니다.")
        if _refs_nonempty(before.source_refs) or not _refs_nonempty(after.source_refs):
            new_exps.append(before)
            continue
        new_exps.append(
            before.model_copy(
                update={
                    "source_refs": after.source_refs,
                    "raw_value": after.raw_value,
                }
            )
        )

    return original.model_copy(update={"jobs": new_jobs, "expertise": new_exps})


def compute_root_evidence_recover(
    repo: AnalysisRepository,
    run: AnalysisRun,
) -> RootEvidenceRecoverComputation:
    before = parse_stored_candidate(run.candidate_json)
    before_storage = before.to_storage_dict()
    page_texts, _doc_types = load_page_context(repo, run)
    allowed = _allowed_documents_from_pages(page_texts)

    # Date repair is verification-only memory; never persisted as project dates.
    dated = backfill_project_dates_from_root_evidence(
        before,
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    supplemented, report = supplement_root_job_exp_evidence_from_project_relations(
        dated,
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    after = _merge_supplemented_root_items(before, supplemented)
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

    changed = after_storage != before_storage
    return RootEvidenceRecoverComputation(
        before_candidate=before,
        after_candidate=after,
        before_storage=before_storage,
        after_storage=after_storage,
        supplemented_jobs=list(report.get("supplemented_jobs") or []),
        supplemented_expertise=list(report.get("supplemented_expertise") or []),
        changed=changed,
    )


def _patch_pending_root_diffs(
    diffs: list[AnalysisDiffItem],
    after: ProfileCandidateDocument,
    *,
    job_codes: set[str],
    exp_codes: set[str],
) -> int:
    """Update PENDING JOB/EXP new_value source_refs only; preserve decisions."""
    job_by_key: dict[tuple[str, str], JobCandidate] = {}
    for job in after.jobs or []:
        code = (job.code or "").strip()
        if code not in job_codes:
            continue
        job_type = (job.job_type or "PRIMARY").strip() or "PRIMARY"
        job_by_key[(code, job_type)] = job
    exp_by_code = {
        (exp.code or "").strip(): exp
        for exp in (after.expertise or [])
        if (exp.code or "").strip() in exp_codes
    }
    updated = 0
    for diff in diffs:
        if diff.review_status != "PENDING":
            continue
        if diff.entity_type not in {"JOB", "EXP"}:
            continue
        nv = diff.new_value
        if not isinstance(nv, dict):
            continue
        code = (nv.get("code") or "").strip()
        if not code:
            continue
        item: JobCandidate | ExpertiseCandidate | None = None
        if diff.entity_type == "JOB":
            job_type = (nv.get("job_type") or "PRIMARY").strip() or "PRIMARY"
            item = job_by_key.get((code, job_type))
        elif code in exp_by_code:
            item = exp_by_code[code]
        if item is None or not _refs_nonempty(item.source_refs):
            continue
        patched = dict(nv)
        patched["source_refs"] = _refs_dump(item.source_refs)
        if item.raw_value is not None:
            patched["raw_value"] = item.raw_value
        # decided_value / review_status / ids untouched.
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
        pending_diffs_updated=0,
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

        diffs = repo.list_diffs(run.id)
        pending_updated = _patch_pending_root_diffs(
            diffs,
            computation.after_candidate,
            job_codes=set(computation.supplemented_jobs),
            exp_codes=set(computation.supplemented_expertise),
        )

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
