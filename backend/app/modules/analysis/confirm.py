"""Analysis Confirm Transaction — apply reviewed diffs to Confirmed Profile.

Runs entirely inside the caller's SQLAlchemy session. Does **not** commit.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AnalysisReviewIncompleteError,
    AnalysisStateConflictError,
    ConfirmValidationError,
    NotFoundError,
    ProfileVersionConflictError,
)
from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.person import (
    Certification,
    Education,
    EmploymentHistory,
    PersonExpertise,
    PersonJob,
    PersonProfile,
    PersonSkill,
)
from app.db.models.project import (
    Project,
    ProjectBusinessDomain,
    ProjectCustomerType,
    ProjectExpertise,
    ProjectJob,
    ProjectSkill,
)
from app.db.models.revision import ProfileRevision
from app.modules.analysis.confirm_dates import assert_date_order, normalize_confirmed_date
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.schemas import ConfirmAnalysisResponseData
from app.modules.career.repository import CareerRepository
from app.modules.people.repository import PeopleRepository
from app.modules.people.snapshot import build_confirmed_profile_snapshot
from app.modules.projects.repository import ProjectRepository

_METADATA_KEYS = frozenset({"source_refs", "confidence", "analysis", "notes"})

_PROFILE_SCALAR_FIELDS = frozenset(
    {
        "name",
        "birth_year",
        "phone",
        "email",
        "address_region",
        "affiliation_company",
        "department",
        "current_title",
        "employment_type",
        "technical_grade",
        "career_start_date",
        "career_document_value",
        "profile_summary",
    }
)
_ALLOWED_GRADES = frozenset(
    {"BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT", "UNKNOWN"}
)
_JOB_TYPES = frozenset({"PRIMARY", "SECONDARY", "EXPERIENCE"})
_EVIDENCE_TYPES = frozenset({"EXPLICIT", "INFERRED"})

_MUST_DECIDE = frozenset({"NEW", "UPDATE", "CONFLICT", "REVIEW"})

_EMPLOYMENT_FIELDS = frozenset(
    {
        "company_name",
        "department",
        "title",
        "start_date",
        "end_date",
        "responsibilities",
    }
)
_EDUCATION_FIELDS = frozenset(
    {"school_name", "major", "degree", "start_date", "end_date", "status"}
)
_CERTIFICATION_FIELDS = frozenset(
    {
        "certification_name",
        "issuer",
        "acquired_date",
        "expiry_date",
        "certificate_no",
    }
)
_PROJECT_SCALAR_FIELDS = frozenset(
    {
        "project_name",
        "customer_name",
        "start_date",
        "end_date",
        "duration_months",
        "responsibilities",
        "project_summary",
    }
)
_PROJECT_RELATION_FIELDS = frozenset(
    {"jobs", "skills", "expertise", "business_domains", "customer_types"}
)

# DB String(n) limits — reject oversize before PostgreSQL DataError/500.
_PROFILE_STRING_LIMITS: dict[str, int] = {
    "name": 150,
    "phone": 50,
    "email": 255,
    "address_region": 200,
    "affiliation_company": 300,
    "department": 200,
    "current_title": 200,
    "employment_type": 50,
    "technical_grade": 30,
    "career_document_value": 100,
}
_EMPLOYMENT_STRING_LIMITS: dict[str, int] = {
    "company_name": 300,
    "department": 200,
    "title": 200,
}
_EDUCATION_STRING_LIMITS: dict[str, int] = {
    "school_name": 300,
    "major": 300,
    "degree": 100,
    "status": 100,
}
_CERTIFICATION_STRING_LIMITS: dict[str, int] = {
    "certification_name": 300,
    "issuer": 300,
    "certificate_no": 200,
}
_PROJECT_STRING_LIMITS: dict[str, int] = {
    "project_name": 500,
    "customer_name": 300,
}
_CAREER_STRING_LIMITS_BY_FIELDS: dict[frozenset[str], dict[str, int]] = {
    _EMPLOYMENT_FIELDS: _EMPLOYMENT_STRING_LIMITS,
    _EDUCATION_FIELDS: _EDUCATION_STRING_LIMITS,
    _CERTIFICATION_FIELDS: _CERTIFICATION_STRING_LIMITS,
}

_PROJECT_ROOT_RE = re.compile(r"^projects\[(\d+)\]$")
_PROJECT_CHILD_RE = re.compile(r"^projects\[(\d+)\]\.(.+)$")
_ENTITY_ROOT_RE = re.compile(
    r"^(employment_history|education|certifications|jobs|skills|expertise)\[\d+\]$"
)

_DATE_BOUNDS: dict[str, str] = {
    "start_date": "start",
    "end_date": "end",
    "acquired_date": "start",
    "expiry_date": "end",
    "career_start_date": "start",
}


def is_project_root(diff: AnalysisDiffItem) -> bool:
    return (
        diff.entity_type == "PROJECT"
        and diff.field_name is None
        and bool(_PROJECT_ROOT_RE.fullmatch(diff.candidate_path or ""))
    )


def parse_project_index(path: str | None) -> int | None:
    if not path:
        return None
    match = _PROJECT_ROOT_RE.fullmatch(path) or _PROJECT_CHILD_RE.fullmatch(path)
    if not match:
        return None
    return int(match.group(1))


def _unwrap_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "source_refs" in value:
        keys = set(value.keys()) - {"value", "source_refs"}
        if not keys:
            return value.get("value")
    return value


def _strip_metadata(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k not in _METADATA_KEYS}


def _applied_value(diff: AnalysisDiffItem) -> Any | None:
    """Return value to apply, or None when the diff should be skipped."""
    status = diff.review_status
    if status in {"REJECTED", "SAME", "PENDING"}:
        return None
    if status == "ACCEPTED":
        return _unwrap_value(diff.new_value)
    if status == "MODIFIED":
        if diff.decided_value is None:
            raise ConfirmValidationError(
                f"MODIFIED Diff에 decided_value가 없습니다: {diff.candidate_path}"
            )
        return _unwrap_value(diff.decided_value)
    if status == "MERGED":
        # Merge uses new_value (+ optional decided_value overrides) in project handler.
        return _unwrap_value(diff.new_value)
    raise ConfirmValidationError(f"허용되지 않은 review_status: {status}")


def _norm_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _assert_str_max_len(field: str, value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        raise ConfirmValidationError(
            f"{field} 길이가 허용 한도({limit})를 초과합니다."
        )
    return text


def _limit_optional_str(
    field: str, value: Any, limits: dict[str, int]
) -> Any:
    if field not in limits:
        return value
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    stripped = text.strip()
    if not stripped:
        return None
    return _assert_str_max_len(field, stripped, limits[field])


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    raise ConfirmValidationError("객체 형태의 값이 필요합니다.")


def _extract_code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _norm_str(value)
    if isinstance(value, dict):
        return _norm_str(
            value.get("code")
            or value.get("job_code")
            or value.get("tech_code")
            or value.get("exp_code")
            or value.get("biz_code")
            or value.get("customer_type_code")
        )
    return None


def _is_entity_root(diff: AnalysisDiffItem) -> bool:
    if diff.field_name is not None:
        return False
    path = diff.candidate_path or ""
    if is_project_root(diff):
        return True
    return bool(_ENTITY_ROOT_RE.fullmatch(path))


def _normalize_field_date(field_name: str, value: Any) -> Any:
    bound = _DATE_BOUNDS.get(field_name)
    if bound is None:
        return value
    try:
        return normalize_confirmed_date(value, bound=bound)  # type: ignore[arg-type]
    except ValueError as exc:
        raise ConfirmValidationError(str(exc)) from exc


def _require_active_code(
    people_repo: PeopleRepository, code: str, expected_type: str
) -> None:
    row = people_repo.get_code(code)
    if row is None or not row.is_active or row.code_type != expected_type:
        raise ConfirmValidationError(
            f"유효하지 않은 {expected_type} 코드입니다: {code}"
        )


def _as_sort_order(raw: Any) -> int:
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool):
        raise ConfirmValidationError(f"sort_order가 올바르지 않습니다: {raw!r}")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfirmValidationError(
            f"sort_order가 올바르지 않습니다: {raw!r}"
        ) from exc


def _as_optional_year(raw: Any, *, field: str = "last_used_year") -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ConfirmValidationError(f"{field}가 올바르지 않습니다: {raw!r}")
    try:
        year = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfirmValidationError(
            f"{field}가 올바르지 않습니다: {raw!r}"
        ) from exc
    if year < 1900 or year > 2100:
        raise ConfirmValidationError(
            f"{field} 범위가 올바르지 않습니다: {year}"
        )
    return year


def _as_optional_months(raw: Any, *, field: str = "experience_months") -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ConfirmValidationError(f"{field}가 올바르지 않습니다: {raw!r}")
    try:
        months = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfirmValidationError(
            f"{field}가 올바르지 않습니다: {raw!r}"
        ) from exc
    if months < 0:
        raise ConfirmValidationError(
            f"{field}는 0 이상이어야 합니다: {months}"
        )
    return months


def _as_bool(raw: Any, *, field: str) -> bool:
    if isinstance(raw, bool):
        return raw
    raise ConfirmValidationError(f"{field}는 boolean이어야 합니다: {raw!r}")


def _parse_job_type(data: dict[str, Any]) -> str:
    """Missing/blank → PRIMARY. Explicit invalid → CONFIRM_VALIDATION_ERROR."""
    if "job_type" not in data:
        return "PRIMARY"
    raw = data.get("job_type")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "PRIMARY"
    text = (_norm_str(raw) or "").upper()
    if text not in _JOB_TYPES:
        raise ConfirmValidationError(f"허용되지 않은 job_type입니다: {raw!r}")
    return text


def _parse_evidence_type(
    data: dict[str, Any],
    *,
    fallback: str | None = None,
) -> str:
    """Missing → fallback/EXPLICIT. Explicit invalid → CONFIRM_VALIDATION_ERROR."""
    if "evidence_type" in data:
        raw = data.get("evidence_type")
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            return (fallback or "EXPLICIT").upper()
        text = (_norm_str(raw) or "").upper()
        if text not in _EVIDENCE_TYPES:
            raise ConfirmValidationError(
                f"허용되지 않은 evidence_type입니다: {raw!r}"
            )
        return text
    if fallback:
        text = (_norm_str(fallback) or "").upper()
        if text and text not in _EVIDENCE_TYPES:
            raise ConfirmValidationError(
                f"허용되지 않은 evidence_type입니다: {fallback!r}"
            )
        if text in _EVIDENCE_TYPES:
            return text
    return "EXPLICIT"


def confirm_analysis_run(
    db: Session,
    *,
    analysis_id: UUID,
    expected_profile_version: int,
    actor_user_id: UUID,
) -> ConfirmAnalysisResponseData:
    """Apply reviewed Analysis Diffs into Confirmed Profile. Does not commit."""
    analysis_repo = AnalysisRepository(db)
    people_repo = PeopleRepository(db)
    career_repo = CareerRepository(db)
    project_repo = ProjectRepository(db)

    # A. AnalysisRun first — serialize review decisions for this run.
    run = analysis_repo.get_run(analysis_id, for_update=True)
    if run is None:
        raise NotFoundError("분석을 찾을 수 없습니다.")

    if run.status == "CONFIRMED":
        return _idempotent_response(db, run)

    if run.status != "REVIEWING":
        raise AnalysisStateConflictError(
            f"상태가 {run.status}인 분석은 확정할 수 없습니다."
        )

    # B. Validate review completeness while AnalysisRun is locked.
    diffs = analysis_repo.list_diffs(run.id)
    _assert_review_complete(diffs)
    _assert_project_parent_consistency(diffs)

    # C. Pre-lock existing mutation targets BEFORE Person/Profile
    #    (matches manual Project/Career: Entity → Person/Profile).
    locked = _prelock_existing_targets(
        person_id=run.person_id,
        diffs=diffs,
        career_repo=career_repo,
        project_repo=project_repo,
    )

    # D. Person/Profile after entity locks — then optimistic version check.
    person = analysis_repo.get_person(run.person_id, for_update=True)
    profile = analysis_repo.get_profile(run.person_id, for_update=True)
    if person is None or profile is None:
        raise NotFoundError("인력을 찾을 수 없습니다.")
    if person.status == "DELETED" or person.deleted_at is not None:
        raise NotFoundError("인력을 찾을 수 없습니다.")

    if (
        expected_profile_version != run.base_profile_version
        or expected_profile_version != profile.profile_version
        or run.base_profile_version != profile.profile_version
    ):
        raise ProfileVersionConflictError()

    ctx = _ConfirmContext(
        db=db,
        run=run,
        person_id=run.person_id,
        profile=profile,
        people_repo=people_repo,
        career_repo=career_repo,
        project_repo=project_repo,
        analysis_repo=analysis_repo,
        now=datetime.now(UTC),
        locked_employment=locked["EMPLOYMENT"],
        locked_education=locked["EDUCATION"],
        locked_certifications=locked["CERTIFICATION"],
        locked_projects=locked["PROJECT"],
    )
    ctx.apply_all(diffs)

    db.flush()
    version = people_repo.bump_profile_version(profile)
    people_repo.touch_person(person)
    snapshot = build_confirmed_profile_snapshot(db, person.id)
    people_repo.add_revision(
        person_id=person.id,
        revision_no=version,
        snapshot=snapshot,
        created_by=actor_user_id,
        source_type="AI_CONFIRMED",
        source_analysis_run_id=run.id,
    )

    counts = _decision_counts(diffs)
    analysis_repo.add_audit(
        action_type="ANALYSIS_CONFIRM",
        actor_user_id=actor_user_id,
        target_type="ANALYSIS_RUN",
        target_id=run.id,
        after={
            "status": "CONFIRMED",
            "profile_version": version,
            "person_id": str(person.id),
        },
        metadata={
            "person_id": str(person.id),
            "base_profile_version": run.base_profile_version,
            "profile_version": version,
            **counts,
        },
    )
    people_repo.enqueue_rebuild_person(person.id, version)

    now = datetime.now(UTC)
    run.status = "CONFIRMED"
    run.confirmed_by = actor_user_id
    run.confirmed_at = now
    run.updated_at = now
    db.add(run)
    db.flush()

    return ConfirmAnalysisResponseData(
        analysis_id=run.id,
        person_id=person.id,
        profile_version=version,
        status="CONFIRMED",
        search_index_status="PENDING",
    )


_MUTABLE_CONFIRM_STATUSES = frozenset({"ACCEPTED", "MODIFIED", "MERGED"})

# Deterministic Confirm pre-lock table order (Entity → Person/Profile).
_PRELOCK_ENTITY_ORDER: tuple[str, ...] = (
    "EMPLOYMENT",
    "EDUCATION",
    "CERTIFICATION",
    "PROJECT",
)


def collect_mutable_existing_target_ids(
    diffs: list[AnalysisDiffItem],
) -> dict[str, list[UUID]]:
    """Collect existing_target_id values for Confirm pre-lock.

    Dedupes IDs and returns UUID-sorted lists per table in lock order.
    """
    buckets: dict[str, set[UUID]] = {name: set() for name in _PRELOCK_ENTITY_ORDER}
    for diff in diffs:
        if diff.review_status not in _MUTABLE_CONFIRM_STATUSES:
            continue
        target_id = diff.existing_target_id
        if target_id is None:
            continue
        if diff.entity_type in buckets:
            buckets[diff.entity_type].add(target_id)
    return {
        name: sorted(buckets[name], key=lambda value: str(value))
        for name in _PRELOCK_ENTITY_ORDER
    }


def _prelock_existing_targets(
    *,
    person_id: UUID,
    diffs: list[AnalysisDiffItem],
    career_repo: CareerRepository,
    project_repo: ProjectRepository,
) -> dict[str, dict[UUID, Any]]:
    """FOR UPDATE existing career/project targets before Person/Profile locks."""
    target_ids = collect_mutable_existing_target_ids(diffs)
    locked: dict[str, dict[UUID, Any]] = {name: {} for name in _PRELOCK_ENTITY_ORDER}

    for employment_id in target_ids["EMPLOYMENT"]:
        row = career_repo.get_employment(employment_id, for_update=True)
        if row is None or row.person_id != person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 경력이 아닙니다: {employment_id}"
            )
        locked["EMPLOYMENT"][employment_id] = row

    for education_id in target_ids["EDUCATION"]:
        row = career_repo.get_education(education_id, for_update=True)
        if row is None or row.person_id != person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 학력이 아닙니다: {education_id}"
            )
        locked["EDUCATION"][education_id] = row

    for certification_id in target_ids["CERTIFICATION"]:
        row = career_repo.get_certification(certification_id, for_update=True)
        if row is None or row.person_id != person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 자격이 아닙니다: "
                f"{certification_id}"
            )
        locked["CERTIFICATION"][certification_id] = row

    for project_id in target_ids["PROJECT"]:
        row = project_repo.get_project(project_id, for_update=True)
        if row is None or row.person_id != person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 프로젝트가 아닙니다: {project_id}"
            )
        locked["PROJECT"][project_id] = row

    return locked


def _idempotent_response(db: Session, run: AnalysisRun) -> ConfirmAnalysisResponseData:
    rev = db.execute(
        select(ProfileRevision).where(
            ProfileRevision.source_analysis_run_id == run.id
        )
    ).scalar_one_or_none()
    if rev is None:
        raise AnalysisStateConflictError(
            "확정된 분석의 ProfileRevision을 찾을 수 없습니다."
        )
    return ConfirmAnalysisResponseData(
        analysis_id=run.id,
        person_id=run.person_id,
        profile_version=rev.revision_no,
        status="CONFIRMED",
        search_index_status="PENDING",
    )


def _assert_review_complete(diffs: list[AnalysisDiffItem]) -> None:
    pending = [
        d
        for d in diffs
        if d.change_type in _MUST_DECIDE and d.review_status == "PENDING"
    ]
    if pending:
        raise AnalysisReviewIncompleteError(
            f"미검토 Diff가 {len(pending)}건 남아 있어 확정할 수 없습니다."
        )


def _assert_project_parent_consistency(diffs: list[AnalysisDiffItem]) -> None:
    rejected: set[int] = set()
    for diff in diffs:
        if is_project_root(diff) and diff.review_status == "REJECTED":
            idx = parse_project_index(diff.candidate_path)
            if idx is not None:
                rejected.add(idx)
    for diff in diffs:
        if diff.review_status not in {"ACCEPTED", "MODIFIED", "MERGED"}:
            continue
        if is_project_root(diff):
            continue
        idx = parse_project_index(diff.candidate_path)
        if idx is not None and idx in rejected:
            raise ConfirmValidationError(
                f"거부된 프로젝트의 하위 Diff를 수락할 수 없습니다: {diff.candidate_path}"
            )


def _decision_counts(diffs: list[AnalysisDiffItem]) -> dict[str, int]:
    counts = {
        "accepted": 0,
        "rejected": 0,
        "modified": 0,
        "merged": 0,
        "same": 0,
        "pending": 0,
    }
    for diff in diffs:
        key = diff.review_status.lower()
        if key in counts:
            counts[key] += 1
    return counts


class _ConfirmContext:
    def __init__(
        self,
        *,
        db: Session,
        run: AnalysisRun,
        person_id: UUID,
        profile: PersonProfile,
        people_repo: PeopleRepository,
        career_repo: CareerRepository,
        project_repo: ProjectRepository,
        analysis_repo: AnalysisRepository,
        now: datetime,
        locked_employment: dict[UUID, EmploymentHistory] | None = None,
        locked_education: dict[UUID, Education] | None = None,
        locked_certifications: dict[UUID, Certification] | None = None,
        locked_projects: dict[UUID, Project] | None = None,
    ) -> None:
        self.db = db
        self.run = run
        self.person_id = person_id
        self.profile = profile
        self.people_repo = people_repo
        self.career_repo = career_repo
        self.project_repo = project_repo
        self.analysis_repo = analysis_repo
        self.now = now
        # projects[n] → Project.id after root create/merge
        self.project_ids_by_index: dict[int, UUID] = {}
        self.locked_employment = locked_employment or {}
        self.locked_education = locked_education or {}
        self.locked_certifications = locked_certifications or {}
        self.locked_projects = locked_projects or {}

    def apply_all(self, diffs: list[AnalysisDiffItem]) -> None:
        project_roots: list[AnalysisDiffItem] = []
        project_children: list[AnalysisDiffItem] = []
        others: list[AnalysisDiffItem] = []

        for diff in diffs:
            if diff.entity_type == "PROJECT":
                if is_project_root(diff):
                    project_roots.append(diff)
                else:
                    project_children.append(diff)
            else:
                others.append(diff)

        for diff in others:
            self._apply_one(diff)
        for diff in project_roots:
            self._apply_project_root(diff)
        for diff in project_children:
            self._apply_project_child(diff)

    def _require_locked_employment(self, employment_id: UUID) -> EmploymentHistory:
        row = self.locked_employment.get(employment_id)
        if row is None or row.person_id != self.person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 경력이 아닙니다: {employment_id}"
            )
        return row

    def _require_locked_education(self, education_id: UUID) -> Education:
        row = self.locked_education.get(education_id)
        if row is None or row.person_id != self.person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 학력이 아닙니다: {education_id}"
            )
        return row

    def _require_locked_certification(
        self, certification_id: UUID
    ) -> Certification:
        row = self.locked_certifications.get(certification_id)
        if row is None or row.person_id != self.person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 자격이 아닙니다: "
                f"{certification_id}"
            )
        return row

    def _apply_one(self, diff: AnalysisDiffItem) -> None:
        if diff.review_status in {"REJECTED", "SAME", "PENDING"}:
            return
        entity = diff.entity_type
        if entity == "PROFILE":
            self._apply_profile(diff)
        elif entity == "JOB":
            self._apply_job(diff)
        elif entity == "TECH":
            self._apply_tech(diff)
        elif entity == "EXP":
            self._apply_exp(diff)
        elif entity == "EMPLOYMENT":
            self._apply_career_entity(
                diff,
                fields=_EMPLOYMENT_FIELDS,
                name_field="company_name",
                getter=self._require_locked_employment,
                creator=self.career_repo.create_employment,
                toucher=self.career_repo.touch_employment,
            )
        elif entity == "EDUCATION":
            self._apply_career_entity(
                diff,
                fields=_EDUCATION_FIELDS,
                name_field="school_name",
                getter=self._require_locked_education,
                creator=self.career_repo.create_education,
                toucher=self.career_repo.touch_education,
            )
        elif entity == "CERTIFICATION":
            self._apply_career_entity(
                diff,
                fields=_CERTIFICATION_FIELDS,
                name_field="certification_name",
                getter=self._require_locked_certification,
                creator=self.career_repo.create_certification,
                toucher=self.career_repo.touch_certification,
            )
        else:
            raise ConfirmValidationError(f"알 수 없는 entity_type: {entity}")

    # ------------------------------------------------------------------ profile

    def _apply_profile(self, diff: AnalysisDiffItem) -> None:
        field = diff.field_name
        if field is None or field not in _PROFILE_SCALAR_FIELDS:
            raise ConfirmValidationError(
                f"허용되지 않은 PROFILE 필드입니다: {field}"
            )
        value = _applied_value(diff)
        if value is None:
            return
        coerced = self._coerce_profile_value(field, value)
        setattr(self.profile, field, coerced)
        self.db.add(self.profile)

    def _coerce_profile_value(self, field: str, value: Any) -> Any:
        if field == "name":
            text = _norm_str(value)
            if not text:
                raise ConfirmValidationError("이름은 비어 있을 수 없습니다.")
            return _assert_str_max_len("name", text, _PROFILE_STRING_LIMITS["name"])
        if field == "birth_year":
            return _as_optional_year(value, field="birth_year")
        if field == "technical_grade":
            text = _norm_str(value)
            if text is None:
                return None
            text = text.upper()
            if text not in _ALLOWED_GRADES:
                raise ConfirmValidationError(
                    f"허용되지 않은 technical_grade입니다: {text}"
                )
            return _assert_str_max_len(
                "technical_grade", text, _PROFILE_STRING_LIMITS["technical_grade"]
            )
        if field == "career_start_date":
            return _normalize_field_date("career_start_date", value)
        if field == "career_document_value":
            if value is None:
                return None
            return _assert_str_max_len(
                "career_document_value",
                str(value),
                _PROFILE_STRING_LIMITS["career_document_value"],
            )
        if field == "profile_summary":
            # Text — no arbitrary length cap.
            if value is None:
                return None
            if isinstance(value, str):
                return value.strip() or None
            return str(value)
        if field in _PROFILE_STRING_LIMITS:
            if value is None or value == "":
                return None
            text = value.strip() if isinstance(value, str) else str(value).strip()
            if not text:
                return None
            return _assert_str_max_len(field, text, _PROFILE_STRING_LIMITS[field])
        if isinstance(value, str):
            return value.strip() or None
        return value

    # -------------------------------------------------------------- JOB/TECH/EXP

    def _apply_job(self, diff: AnalysisDiffItem) -> None:
        if diff.change_type == "SAME":
            return
        value = _applied_value(diff)
        if value is None:
            return
        data = _strip_metadata(_as_dict(value) if not isinstance(value, str) else {"code": value})
        code = _extract_code(data)
        if not code:
            raise ConfirmValidationError(
                f"JOB 코드가 없어 확정할 수 없습니다: {diff.candidate_path}"
            )
        _require_active_code(self.people_repo, code, "JOB")
        job_type = _parse_job_type(data)

        existing = self.db.execute(
            select(PersonJob).where(
                PersonJob.person_id == self.person_id,
                PersonJob.job_code == code,
                PersonJob.job_type == job_type,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        if diff.change_type not in {"NEW", "REVIEW"}:
            # Additive only — unexpected change types with ACCEPTED still insert.
            pass
        self.db.add(
            PersonJob(
                person_id=self.person_id,
                job_code=code,
                job_type=job_type,
                sort_order=_as_sort_order(data.get("sort_order")),
                source_type="AI_CONFIRMED",
                is_active=True,
                confirmed_at=self.now,
            )
        )

    def _apply_tech(self, diff: AnalysisDiffItem) -> None:
        if diff.change_type == "SAME":
            return
        value = _applied_value(diff)
        if value is None:
            return
        data = _strip_metadata(_as_dict(value) if not isinstance(value, str) else {"code": value})
        code = _extract_code(data)
        if not code:
            raise ConfirmValidationError(
                f"TECH 코드가 없어 확정할 수 없습니다: {diff.candidate_path}"
            )
        _require_active_code(self.people_repo, code, "TECH")

        existing = self.db.execute(
            select(PersonSkill).where(
                PersonSkill.person_id == self.person_id,
                PersonSkill.tech_code == code,
            )
        ).scalar_one_or_none()

        status = diff.review_status

        if existing is not None:
            if status == "MODIFIED":
                # Explicit keys only — never fill missing fields with null/default.
                if "last_used_year" in data:
                    existing.last_used_year = _as_optional_year(
                        data.get("last_used_year")
                    )
                if "experience_months" in data:
                    existing.experience_months = _as_optional_months(
                        data.get("experience_months")
                    )
                if "is_representative" in data:
                    existing.is_representative = _as_bool(
                        data.get("is_representative"),
                        field="is_representative",
                    )
                self.db.add(existing)
                return

            if status == "ACCEPTED" and diff.change_type in {
                "UPDATE",
                "NEW",
                "REVIEW",
                "CONFLICT",
            }:
                # Non-destructive: null candidate metadata must not wipe Confirmed.
                last_used = _as_optional_year(data.get("last_used_year"))
                exp_months = _as_optional_months(data.get("experience_months"))
                if last_used is not None:
                    existing.last_used_year = last_used
                if exp_months is not None:
                    existing.experience_months = exp_months
                if "is_representative" in data:
                    rep = _as_bool(
                        data.get("is_representative"),
                        field="is_representative",
                    )
                    # false→true promote only; never auto-demote true→false.
                    if rep:
                        existing.is_representative = True
                # Preserve existing.source_type.
                self.db.add(existing)
            return

        # NEW PersonSkill — Candidate/decided values may create the row.
        if status == "MODIFIED":
            last_used = (
                _as_optional_year(data.get("last_used_year"))
                if "last_used_year" in data
                else None
            )
            exp_months = (
                _as_optional_months(data.get("experience_months"))
                if "experience_months" in data
                else None
            )
            is_rep = (
                _as_bool(data.get("is_representative"), field="is_representative")
                if "is_representative" in data
                else False
            )
        else:
            last_used = _as_optional_year(data.get("last_used_year"))
            exp_months = _as_optional_months(data.get("experience_months"))
            if "is_representative" in data:
                is_rep = _as_bool(
                    data.get("is_representative"), field="is_representative"
                )
            else:
                is_rep = False

        self.db.add(
            PersonSkill(
                person_id=self.person_id,
                tech_code=code,
                last_used_year=last_used,
                experience_months=exp_months,
                is_representative=is_rep,
                source_type="AI_CONFIRMED",
                confirmed_at=self.now,
            )
        )

    def _apply_exp(self, diff: AnalysisDiffItem) -> None:
        if diff.change_type == "SAME":
            return
        value = _applied_value(diff)
        if value is None:
            return
        data = _strip_metadata(_as_dict(value) if not isinstance(value, str) else {"code": value})
        code = _extract_code(data)
        if not code:
            raise ConfirmValidationError(
                f"EXP 코드가 없어 확정할 수 없습니다: {diff.candidate_path}"
            )
        _require_active_code(self.people_repo, code, "EXP")
        evidence = _parse_evidence_type(
            data, fallback=_norm_str(diff.evidence_type)
        )

        existing = self.db.execute(
            select(PersonExpertise).where(
                PersonExpertise.person_id == self.person_id,
                PersonExpertise.exp_code == code,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.evidence_type = evidence
            self.db.add(existing)
            return

        self.db.add(
            PersonExpertise(
                person_id=self.person_id,
                exp_code=code,
                evidence_type=evidence,
                source_type="AI_CONFIRMED",
                confirmed_at=self.now,
            )
        )

    # ----------------------------------------- employment/education/certification

    def _apply_career_entity(
        self,
        diff: AnalysisDiffItem,
        *,
        fields: frozenset[str],
        name_field: str,
        getter,
        creator,
        toucher,
    ) -> None:
        status = diff.review_status
        if status in {"REJECTED", "SAME", "PENDING"}:
            return

        if diff.field_name is not None and diff.existing_target_id is not None:
            self._apply_career_field(
                diff,
                fields=fields,
                getter=getter,
                toucher=toucher,
            )
            return

        if _is_entity_root(diff) or diff.field_name is None:
            if status == "MERGED":
                raise ConfirmValidationError(
                    "MERGED는 PROJECT root REVIEW에만 사용할 수 있습니다."
                )
            value = _applied_value(diff)
            if value is None:
                return
            data = _strip_metadata(_as_dict(value))

            if diff.existing_target_id is not None:
                row = getter(diff.existing_target_id)
                if row is None or row.person_id != self.person_id:
                    raise ConfirmValidationError(
                        f"existing_target_id가 해당 인력의 레코드가 아닙니다: "
                        f"{diff.existing_target_id}"
                    )
                if status == "ACCEPTED":
                    # Null-fill only — do not overwrite non-null fields.
                    self._null_fill_career(row, data, fields=fields)
                elif status == "MODIFIED":
                    # decided_value fields applied explicitly (overwrite).
                    self._overwrite_career(row, data, fields=fields)
                toucher(row)
                return

            # NEW root or REVIEW without target → create
            if diff.change_type in {"NEW", "REVIEW"} or status in {
                "ACCEPTED",
                "MODIFIED",
            }:
                payload = self._career_create_payload(data, fields=fields)
                name = _norm_str(payload.get(name_field))
                if not name:
                    raise ConfirmValidationError(
                        f"{name_field}이(가) 없어 생성할 수 없습니다: "
                        f"{diff.candidate_path}"
                    )
                payload[name_field] = name
                payload["source_type"] = "AI_CONFIRMED"
                creator(self.person_id, **payload)
                return

        raise ConfirmValidationError(
            f"적용할 수 없는 Diff입니다: {diff.candidate_path}"
        )

    def _apply_career_field(
        self, diff: AnalysisDiffItem, *, fields: frozenset[str], getter, toucher
    ) -> None:
        field = diff.field_name
        if field is None or field not in fields:
            raise ConfirmValidationError(f"허용되지 않은 필드입니다: {field}")
        value = _applied_value(diff)
        if value is None:
            return
        row = getter(diff.existing_target_id)
        if row is None or row.person_id != self.person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 레코드가 아닙니다: "
                f"{diff.existing_target_id}"
            )
        # Field UPDATE/CONFLICT: apply that field only; preserve source_type
        coerced = _normalize_field_date(field, value) if field in _DATE_BOUNDS else value
        if isinstance(coerced, str) and field not in _DATE_BOUNDS:
            coerced = coerced.strip() or None
        limits = _CAREER_STRING_LIMITS_BY_FIELDS.get(fields, {})
        coerced = _limit_optional_str(field, coerced, limits)
        setattr(row, field, coerced)
        self._assert_career_dates(row)
        toucher(row)

    def _career_create_payload(
        self, data: dict[str, Any], *, fields: frozenset[str]
    ) -> dict[str, Any]:
        limits = _CAREER_STRING_LIMITS_BY_FIELDS.get(fields, {})
        out: dict[str, Any] = {}
        for key in fields:
            if key not in data:
                continue
            val = data.get(key)
            if val is None or val == "":
                continue
            if key in _DATE_BOUNDS:
                out[key] = _normalize_field_date(key, val)
            elif isinstance(val, str):
                limited = _limit_optional_str(key, val, limits)
                if limited is not None:
                    out[key] = limited
            else:
                limited = _limit_optional_str(key, val, limits)
                out[key] = limited if key in limits else val
        self._assert_date_pair(out)
        return {k: v for k, v in out.items() if v is not None}

    def _null_fill_career(
        self, row: Any, data: dict[str, Any], *, fields: frozenset[str]
    ) -> None:
        limits = _CAREER_STRING_LIMITS_BY_FIELDS.get(fields, {})
        for key in fields:
            if key not in data:
                continue
            current = getattr(row, key, None)
            if current is not None and current != "":
                continue
            val = data.get(key)
            if val is None or val == "":
                continue
            if key in _DATE_BOUNDS:
                val = _normalize_field_date(key, val)
            elif isinstance(val, str):
                val = _limit_optional_str(key, val.strip() or None, limits)
            else:
                val = _limit_optional_str(key, val, limits)
            if val is not None:
                setattr(row, key, val)
        self._assert_career_dates(row)

    def _overwrite_career(
        self, row: Any, data: dict[str, Any], *, fields: frozenset[str]
    ) -> None:
        limits = _CAREER_STRING_LIMITS_BY_FIELDS.get(fields, {})
        for key, val in data.items():
            if key not in fields:
                continue
            if key in _DATE_BOUNDS:
                val = _normalize_field_date(key, val)
            elif isinstance(val, str):
                val = _limit_optional_str(key, val.strip() or None, limits)
            else:
                val = _limit_optional_str(key, val, limits)
            setattr(row, key, val)
        self._assert_career_dates(row)

    def _assert_career_dates(self, row: Any) -> None:
        try:
            if isinstance(row, Certification):
                assert_date_order(
                    row.acquired_date, row.expiry_date, label="certification"
                )
            elif isinstance(row, (EmploymentHistory, Education)):
                assert_date_order(row.start_date, row.end_date, label="period")
        except ValueError as exc:
            raise ConfirmValidationError(str(exc)) from exc

    def _assert_date_pair(self, data: dict[str, Any]) -> None:
        try:
            if "acquired_date" in data or "expiry_date" in data:
                assert_date_order(
                    data.get("acquired_date"),
                    data.get("expiry_date"),
                    label="certification",
                )
            else:
                assert_date_order(
                    data.get("start_date"), data.get("end_date"), label="period"
                )
        except ValueError as exc:
            raise ConfirmValidationError(str(exc)) from exc

    # ----------------------------------------------------------------- projects

    def _apply_project_root(self, diff: AnalysisDiffItem) -> None:
        status = diff.review_status
        idx = parse_project_index(diff.candidate_path)
        if idx is None:
            raise ConfirmValidationError(
                f"잘못된 프로젝트 경로입니다: {diff.candidate_path}"
            )

        if status in {"REJECTED", "SAME", "PENDING"}:
            return

        if status == "MERGED":
            if diff.change_type != "REVIEW":
                raise ConfirmValidationError(
                    "MERGED는 PROJECT root REVIEW에만 사용할 수 있습니다."
                )
            target_id = diff.existing_target_id
            if target_id is None:
                raise ConfirmValidationError("MERGED에는 existing_target_id가 필요합니다.")
            project = self._require_owned_project(target_id)
            base = _strip_metadata(_as_dict(_applied_value(diff)))
            self._null_fill_project_scalars(project, base)
            if diff.decided_value is not None:
                overrides = _strip_metadata(_as_dict(_unwrap_value(diff.decided_value)))
                self._overwrite_project_scalars(project, overrides)
            self._add_project_relations_from_payload(project.id, base)
            if diff.decided_value is not None:
                self._add_project_relations_from_payload(
                    project.id,
                    _strip_metadata(_as_dict(_unwrap_value(diff.decided_value))),
                )
            self.project_repo.touch(project)
            self.project_ids_by_index[idx] = project.id
            return

        if status not in {"ACCEPTED", "MODIFIED"}:
            raise ConfirmValidationError(f"허용되지 않은 review_status: {status}")

        # NEW root or REVIEW root → create new project
        value = _applied_value(diff)
        if value is None:
            return
        data = _strip_metadata(_as_dict(value))
        # MODIFIED may put full entity in decided_value already via _applied_value
        payload = self._project_scalar_payload(data)
        name = _norm_str(payload.get("project_name"))
        if not name:
            raise ConfirmValidationError(
                f"project_name이 없어 프로젝트를 생성할 수 없습니다: "
                f"{diff.candidate_path}"
            )
        payload["project_name"] = name
        project = self.project_repo.create_project(
            self.person_id,
            source_type="AI_CONFIRMED",
            source_analysis_run_id=self.run.id,
            **payload,
        )
        self._add_project_relations_from_payload(project.id, data)
        self.project_ids_by_index[idx] = project.id

    def _apply_project_child(self, diff: AnalysisDiffItem) -> None:
        status = diff.review_status
        if status in {"REJECTED", "SAME", "PENDING"}:
            return
        if status == "MERGED":
            raise ConfirmValidationError(
                "프로젝트 관계/필드 Diff에는 MERGED를 사용할 수 없습니다."
            )

        idx = parse_project_index(diff.candidate_path)
        field = diff.field_name

        # Resolve target project
        project_id = diff.existing_target_id
        if project_id is None and idx is not None:
            project_id = self.project_ids_by_index.get(idx)
        if project_id is None:
            # Parent may have been REJECTED (children must not be ACCEPTED — already checked)
            # or parent SAME without index map — try nothing
            if idx is not None and idx in self.project_ids_by_index:
                project_id = self.project_ids_by_index[idx]
            else:
                # Field update without resolvable target
                raise ConfirmValidationError(
                    f"프로젝트 대상을 찾을 수 없습니다: {diff.candidate_path}"
                )

        project = self._require_owned_project(project_id)
        if idx is not None:
            self.project_ids_by_index.setdefault(idx, project.id)

        if field in _PROJECT_RELATION_FIELDS:
            self._apply_project_relation_diff(diff, project.id, field)
            self.project_repo.touch(project)
            return

        if field in _PROJECT_SCALAR_FIELDS:
            value = _applied_value(diff)
            if value is None:
                return
            coerced = self._coerce_project_scalar(field, value)
            setattr(project, field, coerced)
            try:
                assert_date_order(project.start_date, project.end_date, label="project")
            except ValueError as exc:
                raise ConfirmValidationError(str(exc)) from exc
            self.project_repo.touch(project)
            return

        raise ConfirmValidationError(
            f"허용되지 않은 프로젝트 필드입니다: {field}"
        )

    def _apply_project_relation_diff(
        self, diff: AnalysisDiffItem, project_id: UUID, field: str
    ) -> None:
        value = _applied_value(diff)
        if value is None:
            return
        if diff.change_type == "REVIEW" and diff.review_status == "ACCEPTED":
            code = _extract_code(value)
            if not code:
                # ACCEPTED REVIEW without code — try decided raw? already in value
                raise ConfirmValidationError(
                    f"프로젝트 관계 코드가 없어 확정할 수 없습니다: "
                    f"{diff.candidate_path}"
                )
        item = value if isinstance(value, dict) else {"code": value}
        self._insert_project_relation(project_id, field, item)

    def _require_owned_project(self, project_id: UUID) -> Project:
        project = self.locked_projects.get(project_id)
        if project is None:
            # Newly created in this Confirm TX (not an existing pre-lock target).
            project = self.project_repo.get_project(project_id)
        if project is None or project.person_id != self.person_id:
            raise ConfirmValidationError(
                f"existing_target_id가 해당 인력의 프로젝트가 아닙니다: {project_id}"
            )
        return project

    def _project_scalar_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in _PROJECT_SCALAR_FIELDS:
            if key not in data:
                continue
            val = data.get(key)
            if val is None or val == "":
                continue
            out[key] = self._coerce_project_scalar(key, val)
        try:
            assert_date_order(
                out.get("start_date"), out.get("end_date"), label="project"
            )
        except ValueError as exc:
            raise ConfirmValidationError(str(exc)) from exc
        return out

    def _coerce_project_scalar(self, field: str, value: Any) -> Any:
        if field in {"start_date", "end_date"}:
            return _normalize_field_date(field, value)
        if field == "duration_months":
            months = _as_optional_months(value, field="duration_months")
            return months
        if field in _PROJECT_STRING_LIMITS:
            return _limit_optional_str(field, value, _PROJECT_STRING_LIMITS)
        if isinstance(value, str):
            return value.strip() or None
        return value

    def _null_fill_project_scalars(
        self, project: Project, data: dict[str, Any]
    ) -> None:
        for key in _PROJECT_SCALAR_FIELDS:
            if key not in data:
                continue
            current = getattr(project, key, None)
            if current is not None and current != "":
                continue
            val = data.get(key)
            if val is None or val == "":
                continue
            setattr(project, key, self._coerce_project_scalar(key, val))
        try:
            assert_date_order(project.start_date, project.end_date, label="project")
        except ValueError as exc:
            raise ConfirmValidationError(str(exc)) from exc

    def _overwrite_project_scalars(
        self, project: Project, data: dict[str, Any]
    ) -> None:
        for key, val in data.items():
            if key not in _PROJECT_SCALAR_FIELDS:
                continue
            setattr(project, key, self._coerce_project_scalar(key, val))
        try:
            assert_date_order(project.start_date, project.end_date, label="project")
        except ValueError as exc:
            raise ConfirmValidationError(str(exc)) from exc

    def _add_project_relations_from_payload(
        self, project_id: UUID, data: dict[str, Any]
    ) -> None:
        for field in _PROJECT_RELATION_FIELDS:
            items = data.get(field) or []
            if not isinstance(items, list):
                continue
            for raw in items:
                item = raw if isinstance(raw, dict) else {"code": raw}
                code = _extract_code(item)
                if not code:
                    continue  # skip null codes
                self._insert_project_relation(project_id, field, item)

    def _insert_project_relation(
        self, project_id: UUID, field: str, item: dict[str, Any]
    ) -> None:
        """Additive insert helper — existing relations are never overwritten."""
        code = _extract_code(item)
        if not code:
            raise ConfirmValidationError("프로젝트 관계 코드가 필요합니다.")

        type_map = {
            "jobs": "JOB",
            "skills": "TECH",
            "expertise": "EXP",
            "business_domains": "BIZ",
            "customer_types": "CUSTOMER_TYPE",
        }
        expected = type_map[field]
        _require_active_code(self.people_repo, code, expected)

        if field == "jobs":
            exists = self.db.execute(
                select(ProjectJob).where(
                    ProjectJob.project_id == project_id, ProjectJob.job_code == code
                )
            ).scalar_one_or_none()
            if exists is None:
                self.db.add(ProjectJob(project_id=project_id, job_code=code))
        elif field == "skills":
            exists = self.db.execute(
                select(ProjectSkill).where(
                    ProjectSkill.project_id == project_id,
                    ProjectSkill.tech_code == code,
                )
            ).scalar_one_or_none()
            if exists is None:
                self.db.add(ProjectSkill(project_id=project_id, tech_code=code))
        elif field == "expertise":
            exists = self.db.execute(
                select(ProjectExpertise).where(
                    ProjectExpertise.project_id == project_id,
                    ProjectExpertise.exp_code == code,
                )
            ).scalar_one_or_none()
            if exists is not None:
                # Additive no-op — never mutate existing evidence_type.
                return
            evidence = _parse_evidence_type(item)
            self.db.add(
                ProjectExpertise(
                    project_id=project_id,
                    exp_code=code,
                    evidence_type=evidence,
                )
            )
        elif field == "business_domains":
            exists = self.db.execute(
                select(ProjectBusinessDomain).where(
                    ProjectBusinessDomain.project_id == project_id,
                    ProjectBusinessDomain.biz_code == code,
                )
            ).scalar_one_or_none()
            if exists is None:
                self.db.add(
                    ProjectBusinessDomain(project_id=project_id, biz_code=code)
                )
        elif field == "customer_types":
            exists = self.db.execute(
                select(ProjectCustomerType).where(
                    ProjectCustomerType.project_id == project_id,
                    ProjectCustomerType.customer_type_code == code,
                )
            ).scalar_one_or_none()
            if exists is None:
                self.db.add(
                    ProjectCustomerType(
                        project_id=project_id, customer_type_code=code
                    )
                )


__all__ = [
    "collect_mutable_existing_target_ids",
    "confirm_analysis_run",
    "is_project_root",
    "parse_project_index",
]
