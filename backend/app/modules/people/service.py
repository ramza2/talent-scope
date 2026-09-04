"""People business logic."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidExpCodeError,
    InvalidJobCodeError,
    InvalidPersonStatusError,
    InvalidTechCodeError,
    InvalidTechnicalGradeError,
    NotFoundError,
    ProfileVersionConflictError,
    ValidationAppError,
)
from app.db.models.person import Person, PersonProfile
from app.modules.people.repository import PeopleRepository
from app.modules.people.schemas import (
    ALLOWED_EVIDENCE,
    ALLOWED_GRADES,
    ALLOWED_JOB_TYPES,
    ALLOWED_STATUSES,
    CodeRef,
    DocumentSummary,
    ExpertiseItem,
    ExpertiseReplaceRequest,
    JobItem,
    JobsReplaceRequest,
    PageMeta,
    PendingAnalysis,
    PeopleListItem,
    PersonCreateRequest,
    PersonDetail,
    PersonStatusUpdateRequest,
    ProfileFields,
    ProfileUpdateRequest,
    RecentProject,
    RevisionItem,
    SkillItem,
    SkillsReplaceRequest,
)
from app.modules.people.snapshot import build_confirmed_profile_snapshot


class PeopleService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = PeopleRepository(db)

    def _profile_fields(self, profile: PersonProfile) -> ProfileFields:
        return ProfileFields(
            name=profile.name,
            birth_year=profile.birth_year,
            phone=profile.phone,
            email=profile.email,
            address_region=profile.address_region,
            affiliation_company=profile.affiliation_company,
            department=profile.department,
            current_title=profile.current_title,
            employment_type=profile.employment_type,
            technical_grade=profile.technical_grade,  # type: ignore[arg-type]
            career_start_date=profile.career_start_date,
            career_calculated_months=profile.career_calculated_months,
            career_document_value=profile.career_document_value,
            career_confirmed_months=profile.career_confirmed_months,
            profile_summary=profile.profile_summary,
            profile_updated_at=profile.profile_updated_at,
        )

    def _audit_profile_slice(self, profile: PersonProfile) -> dict:
        return {
            "name": profile.name,
            "birth_year": profile.birth_year,
            "phone": profile.phone,
            "email": profile.email,
            "address_region": profile.address_region,
            "affiliation_company": profile.affiliation_company,
            "department": profile.department,
            "current_title": profile.current_title,
            "employment_type": profile.employment_type,
            "technical_grade": profile.technical_grade,
            "career_start_date": (
                profile.career_start_date.isoformat() if profile.career_start_date else None
            ),
            "career_confirmed_months": profile.career_confirmed_months,
            "profile_version": profile.profile_version,
        }

    def _require_person(self, person_id: UUID, *, for_update: bool = False) -> Person:
        person = self.repo.get_person(person_id, for_update=for_update)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        return person

    def _lock_profile(
        self, person_id: UUID, expected_version: int
    ) -> tuple[Person, PersonProfile]:
        person = self._require_person(person_id, for_update=True)
        profile = self.repo.get_profile(person_id, for_update=True)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")
        if profile.profile_version != expected_version:
            raise ProfileVersionConflictError()
        return person, profile

    def _validate_codes(self, codes: list[str], expected_type: str) -> None:
        if not codes:
            return
        mapping = self.repo.get_codes_map(codes)
        for code in codes:
            row = mapping.get(code)
            if row is None or row.code_type != expected_type:
                if expected_type == "JOB":
                    raise InvalidJobCodeError(f"직무 코드가 올바르지 않습니다: {code}")
                if expected_type == "TECH":
                    raise InvalidTechCodeError(f"기술 코드가 올바르지 않습니다: {code}")
                raise InvalidExpCodeError(f"전문분야 코드가 올바르지 않습니다: {code}")

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
        is_admin: bool,
    ) -> tuple[list[PeopleListItem], PageMeta]:
        if status is not None and status not in ALLOWED_STATUSES:
            raise InvalidPersonStatusError()
        if status == "DELETED" and not is_admin:
            raise InvalidPersonStatusError("DELETED 조회는 관리자만 가능합니다.")
        if grade is not None and grade not in ALLOWED_GRADES:
            raise InvalidTechnicalGradeError()
        if page < 1:
            raise ValidationAppError("page는 1 이상이어야 합니다.")
        if page_size < 1 or page_size > 100:
            raise ValidationAppError("page_size는 1~100 사이여야 합니다.")

        rows, total = self.repo.list_people(
            q=q,
            status=status,
            job_codes=job_codes,
            grade=grade,
            tech_codes=tech_codes,
            exp_codes=exp_codes,
            affiliation=affiliation,
            analysis_status=analysis_status,
            sort=sort or "updated_desc",
            page=page,
            page_size=page_size,
            include_deleted=bool(status == "DELETED" and is_admin),
            is_admin=is_admin,
        )
        person_ids = [person.id for person, _ in rows]
        jobs_map = self.repo.jobs_for_people(person_ids)
        skills_map = self.repo.skills_for_people(person_ids, limit_per_person=5)
        exp_map = self.repo.expertise_for_people(person_ids, limit_per_person=5)

        items: list[PeopleListItem] = []
        for person, profile in rows:
            primary = None
            for job, name in jobs_map.get(person.id, []):
                if job.job_type == "PRIMARY":
                    primary = CodeRef(code=job.job_code, name=name)
                    break
            items.append(
                PeopleListItem(
                    id=person.id,
                    status=person.status,  # type: ignore[arg-type]
                    name=profile.name,
                    primary_job=primary,
                    technical_grade=profile.technical_grade,  # type: ignore[arg-type]
                    career_confirmed_months=profile.career_confirmed_months,
                    affiliation_company=profile.affiliation_company,
                    skills=[
                        CodeRef(code=s.tech_code, name=n)
                        for s, n in skills_map.get(person.id, [])
                    ],
                    expertise=[
                        CodeRef(code=e.exp_code, name=n)
                        for e, n in exp_map.get(person.id, [])
                    ],
                    profile_version=profile.profile_version,
                    profile_updated_at=profile.profile_updated_at,
                    updated_at=person.updated_at,
                )
            )
        meta = PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        )
        return items, meta

    def get_detail(self, person_id: UUID) -> PersonDetail:
        person = self._require_person(person_id)
        profile = self.repo.get_profile(person_id)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")

        jobs = [
            JobItem(
                code=job.job_code,
                name=name,
                job_type=job.job_type,  # type: ignore[arg-type]
                sort_order=job.sort_order,
                source_type=job.source_type,
            )
            for job, name in self.repo.jobs_for_people([person_id]).get(person_id, [])
        ]
        skills = [
            SkillItem(
                code=skill.tech_code,
                name=name,
                last_used_year=skill.last_used_year,
                experience_months=skill.experience_months,
                is_representative=skill.is_representative,
                source_type=skill.source_type,
            )
            for skill, name in self.repo.skills_for_people([person_id]).get(person_id, [])
        ]
        expertise = [
            ExpertiseItem(
                code=exp.exp_code,
                name=name,
                evidence_type=exp.evidence_type,  # type: ignore[arg-type]
                source_type=exp.source_type,
            )
            for exp, name in self.repo.expertise_for_people([person_id]).get(person_id, [])
        ]
        biz = [
            CodeRef(code=c, name=n) for c, n in self.repo.business_domains(person_id)
        ]
        customers = [
            CodeRef(code=c, name=n) for c, n in self.repo.customer_types(person_id)
        ]
        projects = [
            RecentProject(
                id=p.id,
                project_name=p.project_name,
                customer_name=p.customer_name,
                start_date=p.start_date,
                end_date=p.end_date,
            )
            for p in self.repo.recent_projects(person_id)
        ]
        doc_count, latest_doc = self.repo.document_summary(person_id)
        pending = self.repo.pending_analysis(person_id)

        return PersonDetail(
            id=person.id,
            status=person.status,  # type: ignore[arg-type]
            profile_version=profile.profile_version,
            profile=self._profile_fields(profile),
            jobs=jobs,
            skills=skills,
            expertise=expertise,
            business_domains=biz,
            customer_types=customers,
            recent_projects=projects,
            document_summary=DocumentSummary(
                count=doc_count, latest_document_at=latest_doc
            ),
            pending_analysis=(
                PendingAnalysis(id=pending.id, status=pending.status)
                if pending is not None
                else None
            ),
        )

    def create_person(
        self, payload: PersonCreateRequest, actor_user_id: UUID
    ) -> PersonDetail:
        if payload.technical_grade is not None and payload.technical_grade not in ALLOWED_GRADES:
            raise InvalidTechnicalGradeError()

        person = self.repo.create_person(created_by=actor_user_id)
        profile = self.repo.create_profile(
            person.id,
            name=payload.name,
            birth_year=payload.birth_year,
            phone=payload.phone,
            email=payload.email,
            address_region=payload.address_region,
            affiliation_company=payload.affiliation_company,
            department=payload.department,
            current_title=payload.current_title,
            employment_type=payload.employment_type,
            technical_grade=payload.technical_grade,
            career_start_date=payload.career_start_date,
            career_confirmed_months=payload.career_confirmed_months,
            profile_summary=payload.profile_summary,
        )
        snapshot = build_confirmed_profile_snapshot(self.db, person.id)
        self.repo.add_revision(
            person_id=person.id,
            revision_no=profile.profile_version,
            snapshot=snapshot,
            created_by=actor_user_id,
        )
        self.repo.add_audit(
            action_type="PERSON_CREATE",
            actor_user_id=actor_user_id,
            person_id=person.id,
            after=self._audit_profile_slice(profile),
        )
        self.repo.enqueue_rebuild_person(person.id, profile.profile_version)
        self.db.commit()
        return self.get_detail(person.id)

    def update_status(
        self,
        person_id: UUID,
        payload: PersonStatusUpdateRequest,
        actor_user_id: UUID,
    ) -> PersonDetail:
        if payload.status not in ALLOWED_STATUSES:
            raise InvalidPersonStatusError()
        person = self._require_person(person_id, for_update=True)
        before = {"status": person.status, "deleted_at": person.deleted_at.isoformat() if person.deleted_at else None}
        person.status = payload.status
        if payload.status == "DELETED":
            person.deleted_at = datetime.now(UTC)
        else:
            person.deleted_at = None
        self.repo.touch_person(person)
        self.repo.add_audit(
            action_type="PERSON_STATUS_UPDATE",
            actor_user_id=actor_user_id,
            person_id=person.id,
            before=before,
            after={
                "status": person.status,
                "deleted_at": person.deleted_at.isoformat() if person.deleted_at else None,
            },
        )
        self.db.commit()
        return self.get_detail(person.id)

    def update_profile(
        self,
        person_id: UUID,
        payload: ProfileUpdateRequest,
        actor_user_id: UUID,
    ) -> PersonDetail:
        person, profile = self._lock_profile(person_id, payload.expected_profile_version)
        if payload.technical_grade is not None and payload.technical_grade not in ALLOWED_GRADES:
            raise InvalidTechnicalGradeError()

        before = self._audit_profile_slice(profile)
        fields_set = payload.model_fields_set
        mapping = {
            "name": "name",
            "birth_year": "birth_year",
            "phone": "phone",
            "email": "email",
            "address_region": "address_region",
            "affiliation_company": "affiliation_company",
            "department": "department",
            "current_title": "current_title",
            "employment_type": "employment_type",
            "technical_grade": "technical_grade",
            "career_start_date": "career_start_date",
            "career_confirmed_months": "career_confirmed_months",
            "profile_summary": "profile_summary",
        }
        for key, attr in mapping.items():
            if key in fields_set:
                setattr(profile, attr, getattr(payload, key))

        version = self.repo.bump_profile_version(profile)
        self.repo.touch_person(person)
        snapshot = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.add_revision(
            person_id=person_id,
            revision_no=version,
            snapshot=snapshot,
            created_by=actor_user_id,
        )
        self.repo.add_audit(
            action_type="PERSON_PROFILE_UPDATE",
            actor_user_id=actor_user_id,
            person_id=person_id,
            before=before,
            after=self._audit_profile_slice(profile),
        )
        self.repo.enqueue_rebuild_person(person_id, version)
        self.db.commit()
        return self.get_detail(person_id)

    def replace_jobs(
        self,
        person_id: UUID,
        payload: JobsReplaceRequest,
        actor_user_id: UUID,
    ) -> PersonDetail:
        person, profile = self._lock_profile(person_id, payload.expected_profile_version)
        seen: set[tuple[str, str]] = set()
        items = []
        for job in payload.jobs:
            if job.job_type not in ALLOWED_JOB_TYPES:
                raise ValidationAppError("job_type이 올바르지 않습니다.")
            key = (job.job_code, job.job_type)
            if key in seen:
                raise ValidationAppError("동일한 직무 코드/유형이 중복되었습니다.")
            seen.add(key)
            items.append(
                {
                    "job_code": job.job_code,
                    "job_type": job.job_type,
                    "sort_order": job.sort_order,
                }
            )
        self._validate_codes([i["job_code"] for i in items], "JOB")
        before = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.replace_jobs(person_id, items)
        version = self.repo.bump_profile_version(profile)
        self.repo.touch_person(person)
        after = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.add_revision(
            person_id=person_id,
            revision_no=version,
            snapshot=after,
            created_by=actor_user_id,
        )
        self.repo.add_audit(
            action_type="PERSON_JOBS_REPLACE",
            actor_user_id=actor_user_id,
            person_id=person_id,
            before={"jobs": before["jobs"]},
            after={"jobs": after["jobs"]},
        )
        self.repo.enqueue_rebuild_person(person_id, version)
        self.db.commit()
        return self.get_detail(person_id)

    def replace_skills(
        self,
        person_id: UUID,
        payload: SkillsReplaceRequest,
        actor_user_id: UUID,
    ) -> PersonDetail:
        person, profile = self._lock_profile(person_id, payload.expected_profile_version)
        seen: set[str] = set()
        items = []
        for skill in payload.skills:
            if skill.tech_code in seen:
                raise ValidationAppError("동일한 기술 코드가 중복되었습니다.")
            seen.add(skill.tech_code)
            if skill.experience_months is not None and skill.experience_months < 0:
                raise ValidationAppError("experience_months는 0 이상이어야 합니다.")
            items.append(
                {
                    "tech_code": skill.tech_code,
                    "last_used_year": skill.last_used_year,
                    "experience_months": skill.experience_months,
                    "is_representative": skill.is_representative,
                }
            )
        self._validate_codes([i["tech_code"] for i in items], "TECH")
        before = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.replace_skills(person_id, items)
        version = self.repo.bump_profile_version(profile)
        self.repo.touch_person(person)
        after = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.add_revision(
            person_id=person_id,
            revision_no=version,
            snapshot=after,
            created_by=actor_user_id,
        )
        self.repo.add_audit(
            action_type="PERSON_SKILLS_REPLACE",
            actor_user_id=actor_user_id,
            person_id=person_id,
            before={"skills": before["skills"]},
            after={"skills": after["skills"]},
        )
        self.repo.enqueue_rebuild_person(person_id, version)
        self.db.commit()
        return self.get_detail(person_id)

    def replace_expertise(
        self,
        person_id: UUID,
        payload: ExpertiseReplaceRequest,
        actor_user_id: UUID,
    ) -> PersonDetail:
        person, profile = self._lock_profile(person_id, payload.expected_profile_version)
        seen: set[str] = set()
        items = []
        for exp in payload.expertise:
            if exp.exp_code in seen:
                raise ValidationAppError("동일한 전문분야 코드가 중복되었습니다.")
            seen.add(exp.exp_code)
            if exp.evidence_type not in ALLOWED_EVIDENCE:
                raise ValidationAppError("evidence_type이 올바르지 않습니다.")
            items.append(
                {
                    "exp_code": exp.exp_code,
                    "evidence_type": exp.evidence_type,
                }
            )
        self._validate_codes([i["exp_code"] for i in items], "EXP")
        before = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.replace_expertise(person_id, items)
        version = self.repo.bump_profile_version(profile)
        self.repo.touch_person(person)
        after = build_confirmed_profile_snapshot(self.db, person_id)
        self.repo.add_revision(
            person_id=person_id,
            revision_no=version,
            snapshot=after,
            created_by=actor_user_id,
        )
        self.repo.add_audit(
            action_type="PERSON_EXPERTISE_REPLACE",
            actor_user_id=actor_user_id,
            person_id=person_id,
            before={"expertise": before["expertise"]},
            after={"expertise": after["expertise"]},
        )
        self.repo.enqueue_rebuild_person(person_id, version)
        self.db.commit()
        return self.get_detail(person_id)

    def list_revisions(
        self, person_id: UUID, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[RevisionItem], PageMeta]:
        self._require_person(person_id)
        if page < 1:
            raise ValidationAppError("page는 1 이상이어야 합니다.")
        if page_size < 1 or page_size > 100:
            raise ValidationAppError("page_size는 1~100 사이여야 합니다.")
        rows, total = self.repo.list_revisions(person_id, page=page, page_size=page_size)
        items = [
            RevisionItem(
                revision_no=rev.revision_no,
                source_type=rev.source_type,
                created_by=rev.created_by,
                created_by_name=name,
                created_at=rev.created_at,
                snapshot=rev.snapshot_json,
            )
            for rev, name in rows
        ]
        meta = PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        )
        return items, meta
