"""Guard-only refresh for REVIEWING analysis candidates (no AI re-analysis).

Re-applies ``apply_candidate_quality_guards`` to stored ``candidate_json`` using
the analysis run's linked document page text / doc types, then rebuilds diffs
against the base ``ProfileRevision`` snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.core.exceptions import (
    AnalysisStateConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models.analysis import AnalysisRun
from app.db.models.person import PersonProfile
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.diff_engine import DiffSpec, build_diffs
from app.modules.analysis.repository import AnalysisRepository


@dataclass(frozen=True)
class CandidateEntityCounts:
    education: int
    certifications: int
    employment: int
    projects: int

    def as_dict(self) -> dict[str, int]:
        return {
            "education": self.education,
            "certifications": self.certifications,
            "employment": self.employment,
            "projects": self.projects,
        }


@dataclass(frozen=True)
class GuardRefreshComputation:
    before_counts: CandidateEntityCounts
    after_counts: CandidateEntityCounts
    before_candidate: ProfileCandidateDocument
    after_candidate: ProfileCandidateDocument
    before_storage: dict[str, Any]
    after_storage: dict[str, Any]
    specs: list[DiffSpec]
    diff_count_before: int
    pending_diff_count_before: int
    changed: bool
    base_snapshot: dict[str, Any]


@dataclass(frozen=True)
class GuardRefreshResult:
    analysis_id: UUID
    status: str
    changed: bool
    applied: bool
    ui_refresh_required: bool
    before_counts: CandidateEntityCounts
    after_counts: CandidateEntityCounts
    diff_count_before: int
    diff_count_after: int
    pending_diff_count_before: int
    pending_diff_count_after: int


def entity_counts(candidate: ProfileCandidateDocument) -> CandidateEntityCounts:
    return CandidateEntityCounts(
        education=len(candidate.education or []),
        certifications=len(candidate.certifications or []),
        employment=len(candidate.employment_history or []),
        projects=len(candidate.projects or []),
    )


def parse_stored_candidate(raw: Any) -> ProfileCandidateDocument:
    if not isinstance(raw, dict) or not raw:
        raise ValidationAppError("저장된 후보(candidate_json)가 비어 있습니다.")
    try:
        return ProfileCandidateDocument.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — surface as validation
        raise ValidationAppError(
            f"저장된 후보(candidate_json)를 읽을 수 없습니다: {exc}"
        ) from exc


def load_page_context(
    repo: AnalysisRepository,
    run: AnalysisRun,
) -> tuple[dict[tuple[str, int], str], dict[str, str]]:
    """Load original page texts and document types for the analysis documents."""
    doc_ids = repo.list_run_document_ids(run.id)
    if not doc_ids:
        raise AnalysisStateConflictError(
            "분석에 연결된 문서가 없어 Guard 새로고침을 할 수 없습니다."
        )
    rows = repo.list_documents_by_ids(doc_ids)
    if len(rows) != len(doc_ids):
        raise AnalysisStateConflictError(
            "분석에 연결된 문서 일부가 삭제되었거나 유효하지 않습니다."
        )
    page_texts: dict[tuple[str, int], str] = {}
    document_type_by_id: dict[str, str] = {}
    for document, group, _type_name in rows:
        if group.person_id != run.person_id:
            raise AnalysisStateConflictError(
                "분석 문서와 인력 관계가 일치하지 않습니다."
            )
        code = (group.document_type_code or "").strip()
        if code:
            document_type_by_id[str(document.id)] = code
        for page in repo.list_document_pages(document.id):
            text = (page.extracted_text or "").strip()
            if not text:
                continue
            page_texts[(str(document.id), int(page.page_no))] = text
    return page_texts, document_type_by_id


def assert_guard_refresh_eligible(
    repo: AnalysisRepository,
    run: AnalysisRun,
    profile: PersonProfile | None,
) -> dict[str, Any]:
    """Validate REVIEWING + all-PENDING + version + revision + docs.

    Returns the base ProfileRevision snapshot.
    """
    if run.status in {"CONFIRMED", "CANCELLED"}:
        raise AnalysisStateConflictError(
            f"{run.status} 상태의 분석에는 Guard 새로고침을 할 수 없습니다."
        )
    if run.status != "REVIEWING":
        raise AnalysisStateConflictError(
            "REVIEWING 상태의 분석만 Guard 새로고침을 할 수 있습니다."
        )
    if run.base_profile_version is None:
        raise AnalysisStateConflictError(
            "기준 프로필 버전이 없어 Guard 새로고침을 할 수 없습니다."
        )
    if profile is None:
        raise NotFoundError("프로필을 찾을 수 없습니다.")
    if int(profile.profile_version) != int(run.base_profile_version):
        raise AnalysisStateConflictError(
            "프로필 버전이 변경되어 Guard 새로고침을 할 수 없습니다."
        )

    diffs = repo.list_diffs(run.id)
    if any(d.review_status != "PENDING" for d in diffs):
        raise AnalysisStateConflictError(
            "이미 검토된 Diff가 있어 Guard 새로고침을 할 수 없습니다."
        )

    snapshot = repo.get_profile_revision_snapshot(
        run.person_id, int(run.base_profile_version)
    )
    if snapshot is None:
        raise AnalysisStateConflictError(
            "기준 ProfileRevision snapshot이 없어 Guard 새로고침을 할 수 없습니다."
        )

    # Validate document relationships (raises on invalid).
    load_page_context(repo, run)
    return snapshot


def compute_guard_refresh(
    repo: AnalysisRepository,
    run: AnalysisRun,
    *,
    base_snapshot: dict[str, Any],
) -> GuardRefreshComputation:
    before_candidate = parse_stored_candidate(run.candidate_json)
    before_storage = before_candidate.to_storage_dict()
    before_counts = entity_counts(before_candidate)

    page_texts, document_type_by_id = load_page_context(repo, run)
    base_profile = (
        base_snapshot.get("profile") if isinstance(base_snapshot, dict) else None
    )
    confirmed_name = (
        base_profile.get("name") if isinstance(base_profile, dict) else None
    )

    after_candidate = apply_candidate_quality_guards(
        before_candidate,
        confirmed_person_name=confirmed_name,
        page_texts=page_texts,
        document_type_by_id=document_type_by_id,
    )
    after_storage = after_candidate.to_storage_dict()
    after_counts = entity_counts(after_candidate)
    changed = after_storage != before_storage

    diffs = repo.list_diffs(run.id)
    diff_count_before = len(diffs)
    pending_before = sum(1 for d in diffs if d.review_status == "PENDING")

    specs: list[DiffSpec] = []
    if changed:
        specs = build_diffs(after_candidate, base_snapshot)

    return GuardRefreshComputation(
        before_counts=before_counts,
        after_counts=after_counts,
        before_candidate=before_candidate,
        after_candidate=after_candidate,
        before_storage=before_storage,
        after_storage=after_storage,
        specs=specs,
        diff_count_before=diff_count_before,
        pending_diff_count_before=pending_before,
        changed=changed,
        base_snapshot=base_snapshot,
    )


def preview_guard_refresh(
    db: Session, analysis_id: UUID
) -> GuardRefreshResult:
    """Read-only preview — no writes / no locks required for mutation safety."""
    repo = AnalysisRepository(db)
    run = repo.get_run(analysis_id)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")
    profile = repo.get_profile(run.person_id)
    snapshot = assert_guard_refresh_eligible(repo, run, profile)
    computation = compute_guard_refresh(repo, run, base_snapshot=snapshot)
    diff_after = (
        len(computation.specs)
        if computation.changed
        else computation.diff_count_before
    )
    pending_after = diff_after if computation.changed else computation.pending_diff_count_before
    return GuardRefreshResult(
        analysis_id=run.id,
        status=run.status,
        changed=computation.changed,
        applied=False,
        ui_refresh_required=False,
        before_counts=computation.before_counts,
        after_counts=computation.after_counts,
        diff_count_before=computation.diff_count_before,
        diff_count_after=diff_after,
        pending_diff_count_before=computation.pending_diff_count_before,
        pending_diff_count_after=pending_after,
    )


def apply_guard_refresh(
    db: Session,
    analysis_id: UUID,
    *,
    actor_user_id: UUID,
) -> GuardRefreshResult:
    """Apply guard refresh under row locks in a single transaction."""
    repo = AnalysisRepository(db)

    peek = repo.get_run(analysis_id)
    if peek is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")

    # Lock order: person → profile → analysis_run (align with confirm/review).
    person = repo.get_person(peek.person_id, for_update=True)
    if person is None or person.deleted_at is not None or person.status == "DELETED":
        raise NotFoundError("인력을 찾을 수 없습니다.")

    profile = repo.get_profile(peek.person_id, for_update=True)
    run = repo.get_run(analysis_id, for_update=True)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")

    snapshot = assert_guard_refresh_eligible(repo, run, profile)
    computation = compute_guard_refresh(repo, run, base_snapshot=snapshot)

    if not computation.changed:
        # No candidate change — leave diffs untouched.
        db.commit()
        return GuardRefreshResult(
            analysis_id=run.id,
            status=run.status,
            changed=False,
            applied=False,
            ui_refresh_required=False,
            before_counts=computation.before_counts,
            after_counts=computation.after_counts,
            diff_count_before=computation.diff_count_before,
            diff_count_after=computation.diff_count_before,
            pending_diff_count_before=computation.pending_diff_count_before,
            pending_diff_count_after=computation.pending_diff_count_before,
        )

    try:
        repo.replace_diffs(
            run.id, [spec.to_persist_dict() for spec in computation.specs]
        )
        run.candidate_json = computation.after_storage
        confidence = computation.after_candidate.analysis.overall_confidence
        run.overall_confidence = confidence
        run.updated_at = datetime.now(UTC)
        db.add(run)
        db.flush()

        new_diff_count = len(computation.specs)
        repo.add_audit(
            action_type="ANALYSIS_GUARD_REFRESH",
            actor_user_id=actor_user_id,
            target_id=run.id,
            before={
                "status": "REVIEWING",
                "entity_counts": computation.before_counts.as_dict(),
                "diff_count": computation.diff_count_before,
                "pending_diff_count": computation.pending_diff_count_before,
            },
            after={
                "status": "REVIEWING",
                "entity_counts": computation.after_counts.as_dict(),
                "diff_count": new_diff_count,
                "pending_diff_count": new_diff_count,
                "ui_refresh_required": True,
            },
            metadata={
                "operation": "guard_refresh",
                "changed": True,
                "base_profile_version": run.base_profile_version,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return GuardRefreshResult(
        analysis_id=run.id,
        status=run.status,
        changed=True,
        applied=True,
        ui_refresh_required=True,
        before_counts=computation.before_counts,
        after_counts=computation.after_counts,
        diff_count_before=computation.diff_count_before,
        diff_count_after=new_diff_count,
        pending_diff_count_before=computation.pending_diff_count_before,
        pending_diff_count_after=new_diff_count,
    )
