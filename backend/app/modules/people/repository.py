"""People DB access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.models.code import CodeAlias, CodeMaster
from app.db.models.document import Document, DocumentGroup
from app.db.models.person import (
    Person,
    PersonExpertise,
    PersonJob,
    PersonProfile,
    PersonSkill,
)
from app.db.models.project import Project
from app.db.models.revision import AuditLog, ProfileRevision
from app.db.models.search import SearchIndexJob
from app.db.models.user import AppUser


def _parse_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class PeopleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_person(self, person_id: UUID, *, for_update: bool = False) -> Person | None:
        stmt = select(Person).where(Person.id == person_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_profile(
        self, person_id: UUID, *, for_update: bool = False
    ) -> PersonProfile | None:
        stmt = select(PersonProfile).where(PersonProfile.person_id == person_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_code(self, code: str) -> CodeMaster | None:
        return self.db.execute(
            select(CodeMaster).where(CodeMaster.code == code)
        ).scalar_one_or_none()

    def get_codes_map(self, codes: list[str]) -> dict[str, CodeMaster]:
        if not codes:
            return {}
        rows = self.db.execute(
            select(CodeMaster).where(CodeMaster.code.in_(codes))
        ).scalars().all()
        return {row.code: row for row in rows}

    def list_people(
        self,
        *,
        q: str | None,
        status: str | None,
        job_codes: str | None,
        grade: str | None,
        tech_codes: str | None,
        exp_codes: str | None,
        affiliation: str | None,
        analysis_status: str | None,
        sort: str,
        page: int,
        page_size: int,
        include_deleted: bool,
        is_admin: bool,
    ) -> tuple[list[tuple[Person, PersonProfile]], int]:
        filters = []
        if status:
            filters.append(Person.status == status)
            if status != "DELETED":
                filters.append(Person.deleted_at.is_(None))
        else:
            if not include_deleted:
                filters.append(Person.status != "DELETED")
                filters.append(Person.deleted_at.is_(None))

        if grade:
            filters.append(PersonProfile.technical_grade == grade)
        if affiliation:
            filters.append(PersonProfile.affiliation_company.ilike(f"%{affiliation.strip()}%"))

        jobs = _parse_codes(job_codes)
        techs = _parse_codes(tech_codes)
        exps = _parse_codes(exp_codes)

        if jobs:
            filters.append(
                Person.id.in_(
                    select(PersonJob.person_id).where(
                        PersonJob.job_code.in_(jobs),
                        PersonJob.is_active.is_(True),
                    )
                )
            )
        if techs:
            filters.append(
                Person.id.in_(
                    select(PersonSkill.person_id).where(PersonSkill.tech_code.in_(techs))
                )
            )
        if exps:
            filters.append(
                Person.id.in_(
                    select(PersonExpertise.person_id).where(
                        PersonExpertise.exp_code.in_(exps)
                    )
                )
            )

        if analysis_status:
            filters.append(
                Person.id.in_(
                    select(AnalysisRun.person_id).where(
                        AnalysisRun.status == analysis_status
                    )
                )
            )

        if q:
            pattern = f"%{q.strip()}%"
            q_norm = q.strip()
            code_match = (
                select(CodeMaster.code)
                .where(
                    or_(
                        CodeMaster.name.ilike(pattern),
                        CodeMaster.code.ilike(pattern),
                        CodeMaster.code.in_(
                            select(CodeAlias.code).where(
                                or_(
                                    CodeAlias.alias.ilike(pattern),
                                    CodeAlias.normalized_alias.ilike(f"%{q_norm.casefold()}%"),
                                )
                            )
                        ),
                    )
                )
            )
            filters.append(
                or_(
                    PersonProfile.name.ilike(pattern),
                    PersonProfile.email.ilike(pattern),
                    PersonProfile.phone.ilike(pattern),
                    PersonProfile.affiliation_company.ilike(pattern),
                    PersonProfile.department.ilike(pattern),
                    PersonProfile.current_title.ilike(pattern),
                    Person.id.in_(
                        select(PersonJob.person_id).where(PersonJob.job_code.in_(code_match))
                    ),
                    Person.id.in_(
                        select(PersonSkill.person_id).where(
                            PersonSkill.tech_code.in_(code_match)
                        )
                    ),
                    Person.id.in_(
                        select(PersonExpertise.person_id).where(
                            PersonExpertise.exp_code.in_(code_match)
                        )
                    ),
                )
            )

        base = (
            select(Person, PersonProfile)
            .join(PersonProfile, PersonProfile.person_id == Person.id)
        )
        count_stmt = (
            select(func.count())
            .select_from(Person)
            .join(PersonProfile, PersonProfile.person_id == Person.id)
        )
        for f in filters:
            base = base.where(f)
            count_stmt = count_stmt.where(f)

        total = int(self.db.execute(count_stmt).scalar_one())

        if sort == "name_asc":
            base = base.order_by(PersonProfile.name.asc(), Person.updated_at.desc())
        elif sort == "updated_asc":
            base = base.order_by(Person.updated_at.asc())
        else:
            base = base.order_by(Person.updated_at.desc())

        rows = list(
            self.db.execute(
                base.offset((page - 1) * page_size).limit(page_size)
            ).all()
        )
        return rows, total

    def jobs_for_people(self, person_ids: list[UUID]) -> dict[UUID, list[tuple[PersonJob, str]]]:
        if not person_ids:
            return {}
        rows = self.db.execute(
            select(PersonJob, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonJob.job_code)
            .where(PersonJob.person_id.in_(person_ids), PersonJob.is_active.is_(True))
            .order_by(PersonJob.sort_order.asc(), PersonJob.job_code.asc())
        ).all()
        result: dict[UUID, list[tuple[PersonJob, str]]] = {pid: [] for pid in person_ids}
        for job, name in rows:
            result.setdefault(job.person_id, []).append((job, name or job.job_code))
        return result

    def skills_for_people(
        self, person_ids: list[UUID], *, limit_per_person: int | None = None
    ) -> dict[UUID, list[tuple[PersonSkill, str]]]:
        if not person_ids:
            return {}
        rows = self.db.execute(
            select(PersonSkill, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonSkill.tech_code)
            .where(PersonSkill.person_id.in_(person_ids))
            .order_by(
                PersonSkill.is_representative.desc(),
                PersonSkill.tech_code.asc(),
            )
        ).all()
        result: dict[UUID, list[tuple[PersonSkill, str]]] = {pid: [] for pid in person_ids}
        for skill, name in rows:
            bucket = result.setdefault(skill.person_id, [])
            if limit_per_person is not None and len(bucket) >= limit_per_person:
                continue
            bucket.append((skill, name or skill.tech_code))
        return result

    def expertise_for_people(
        self, person_ids: list[UUID], *, limit_per_person: int | None = None
    ) -> dict[UUID, list[tuple[PersonExpertise, str]]]:
        if not person_ids:
            return {}
        rows = self.db.execute(
            select(PersonExpertise, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonExpertise.exp_code)
            .where(PersonExpertise.person_id.in_(person_ids))
            .order_by(PersonExpertise.exp_code.asc())
        ).all()
        result: dict[UUID, list[tuple[PersonExpertise, str]]] = {
            pid: [] for pid in person_ids
        }
        for exp, name in rows:
            bucket = result.setdefault(exp.person_id, [])
            if limit_per_person is not None and len(bucket) >= limit_per_person:
                continue
            bucket.append((exp, name or exp.exp_code))
        return result

    def create_person(self, *, created_by: UUID) -> Person:
        person = Person(status="ACTIVE", created_by=created_by)
        self.db.add(person)
        self.db.flush()
        return person

    def create_profile(self, person_id: UUID, **fields: Any) -> PersonProfile:
        profile = PersonProfile(person_id=person_id, profile_version=1, **fields)
        self.db.add(profile)
        self.db.flush()
        return profile

    def replace_jobs(self, person_id: UUID, items: list[dict[str, Any]]) -> None:
        self.db.execute(delete(PersonJob).where(PersonJob.person_id == person_id))
        now = datetime.now(UTC)
        for item in items:
            self.db.add(
                PersonJob(
                    person_id=person_id,
                    job_code=item["job_code"],
                    job_type=item["job_type"],
                    sort_order=item.get("sort_order", 0),
                    source_type="USER",
                    is_active=True,
                    confirmed_at=now,
                )
            )
        self.db.flush()

    def replace_skills(self, person_id: UUID, items: list[dict[str, Any]]) -> None:
        self.db.execute(delete(PersonSkill).where(PersonSkill.person_id == person_id))
        now = datetime.now(UTC)
        for item in items:
            self.db.add(
                PersonSkill(
                    person_id=person_id,
                    tech_code=item["tech_code"],
                    last_used_year=item.get("last_used_year"),
                    experience_months=item.get("experience_months"),
                    is_representative=bool(item.get("is_representative", False)),
                    source_type="USER",
                    confirmed_at=now,
                )
            )
        self.db.flush()

    def replace_expertise(self, person_id: UUID, items: list[dict[str, Any]]) -> None:
        self.db.execute(
            delete(PersonExpertise).where(PersonExpertise.person_id == person_id)
        )
        now = datetime.now(UTC)
        for item in items:
            self.db.add(
                PersonExpertise(
                    person_id=person_id,
                    exp_code=item["exp_code"],
                    evidence_type=item.get("evidence_type", "EXPLICIT"),
                    source_type="USER",
                    confirmed_at=now,
                )
            )
        self.db.flush()

    def bump_profile_version(self, profile: PersonProfile) -> int:
        now = datetime.now(UTC)
        profile.profile_version += 1
        profile.profile_updated_at = now
        profile.updated_at = now
        self.db.add(profile)
        self.db.flush()
        return profile.profile_version

    def touch_person(self, person: Person) -> None:
        person.updated_at = datetime.now(UTC)
        self.db.add(person)

    def add_revision(
        self,
        *,
        person_id: UUID,
        revision_no: int,
        snapshot: dict[str, Any],
        created_by: UUID | None,
        source_type: str = "USER",
    ) -> ProfileRevision:
        rev = ProfileRevision(
            person_id=person_id,
            revision_no=revision_no,
            snapshot_json=snapshot,
            source_type=source_type,
            created_by=created_by,
        )
        self.db.add(rev)
        self.db.flush()
        return rev

    def add_audit(
        self,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        person_id: UUID,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action_type=action_type,
                target_type="PERSON",
                target_id=person_id,
                before_json=before,
                after_json=after,
                metadata_json=metadata or {},
            )
        )

    def enqueue_rebuild_person(self, person_id: UUID, profile_version: int) -> None:
        key = f"people:{person_id}:profile:{profile_version}:rebuild"
        existing = self.db.execute(
            select(SearchIndexJob).where(SearchIndexJob.idempotency_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            return
        self.db.add(
            SearchIndexJob(
                person_id=person_id,
                action="REBUILD_PERSON",
                status="PENDING",
                idempotency_key=key,
                payload_json={"profile_version": profile_version},
            )
        )

    def list_revisions(
        self, person_id: UUID, *, page: int, page_size: int
    ) -> tuple[list[tuple[ProfileRevision, str | None]], int]:
        total = int(
            self.db.execute(
                select(func.count())
                .select_from(ProfileRevision)
                .where(ProfileRevision.person_id == person_id)
            ).scalar_one()
        )
        rows = list(
            self.db.execute(
                select(ProfileRevision, AppUser.name)
                .outerjoin(AppUser, AppUser.id == ProfileRevision.created_by)
                .where(ProfileRevision.person_id == person_id)
                .order_by(ProfileRevision.revision_no.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def recent_projects(self, person_id: UUID, *, limit: int = 5) -> list[Project]:
        return list(
            self.db.execute(
                select(Project)
                .where(Project.person_id == person_id, Project.deleted_at.is_(None))
                .order_by(Project.start_date.desc().nullslast(), Project.created_at.desc())
                .limit(limit)
            ).scalars().all()
        )

    def business_domains(self, person_id: UUID) -> list[tuple[str, str]]:
        rows = self.db.execute(
            text(
                """
                SELECT DISTINCT pbd.biz_code, cm.name
                FROM project p
                JOIN project_business_domain pbd ON pbd.project_id = p.id
                LEFT JOIN code_master cm ON cm.code = pbd.biz_code
                WHERE p.person_id = :pid AND p.deleted_at IS NULL
                ORDER BY pbd.biz_code
                """
            ),
            {"pid": person_id},
        ).all()
        return [(r[0], r[1] or r[0]) for r in rows]

    def customer_types(self, person_id: UUID) -> list[tuple[str, str]]:
        rows = self.db.execute(
            text(
                """
                SELECT DISTINCT pct.customer_type_code, cm.name
                FROM project p
                JOIN project_customer_type pct ON pct.project_id = p.id
                LEFT JOIN code_master cm ON cm.code = pct.customer_type_code
                WHERE p.person_id = :pid AND p.deleted_at IS NULL
                ORDER BY pct.customer_type_code
                """
            ),
            {"pid": person_id},
        ).all()
        return [(r[0], r[1] or r[0]) for r in rows]

    def document_summary(self, person_id: UUID) -> tuple[int, datetime | None]:
        row = self.db.execute(
            select(
                func.count(Document.id),
                func.max(Document.uploaded_at),
            )
            .select_from(Document)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .where(
                DocumentGroup.person_id == person_id,
                DocumentGroup.deleted_at.is_(None),
                Document.deleted_at.is_(None),
            )
        ).one()
        return int(row[0] or 0), row[1]

    def pending_analysis(self, person_id: UUID) -> AnalysisRun | None:
        return self.db.execute(
            select(AnalysisRun)
            .where(
                AnalysisRun.person_id == person_id,
                AnalysisRun.status.in_(("QUEUED", "PROCESSING", "REVIEWING")),
            )
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
