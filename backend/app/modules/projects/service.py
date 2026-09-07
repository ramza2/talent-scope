"""Project business logic."""

from __future__ import annotations

import math
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    InvalidProjectBizCodeError,
    InvalidProjectCustomerTypeCodeError,
    InvalidProjectExpCodeError,
    InvalidProjectJobCodeError,
    InvalidProjectTechCodeError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models.project import Project
from app.modules.people.finalize import finalize_confirmed_profile_change
from app.modules.people.repository import PeopleRepository
from app.modules.people.schemas import CodeRef, PageMeta
from app.modules.people.visibility import ensure_person_readable
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    ALLOWED_EVIDENCE,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectExpertiseItem,
    ProjectExpertiseWrite,
    ProjectUpdateRequest,
)


class ProjectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = ProjectRepository(db)
        self.people_repo = PeopleRepository(db)

    def _require_person_profile(
        self,
        person_id: UUID,
        *,
        for_update: bool = False,
        is_admin: bool | None = None,
    ):
        person = self.people_repo.get_person(person_id, for_update=for_update)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        if is_admin is not None:
            ensure_person_readable(person, is_admin=is_admin)
        profile = self.people_repo.get_profile(person_id, for_update=for_update)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")
        return person, profile

    def _require_project(
        self, project_id: UUID, *, for_update: bool = False
    ) -> Project:
        project = self.repo.get_project(project_id, for_update=for_update)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없습니다.")
        return project

    def _dedupe_codes(self, codes: list[str], *, label: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for code in codes:
            cleaned = code.strip()
            if not cleaned:
                raise ValidationAppError(f"{label} 코드가 비어 있습니다.")
            if cleaned in seen:
                raise ValidationAppError(f"{label} 코드가 중복되었습니다: {cleaned}")
            seen.add(cleaned)
            result.append(cleaned)
        return result

    def _validate_codes(
        self, codes: list[str], expected_type: str, error_cls: type[Exception]
    ) -> None:
        if not codes:
            return
        mapping = self.repo.get_codes_map(codes)
        for code in codes:
            row = mapping.get(code)
            if row is None or row.code_type != expected_type:
                raise error_cls(f"{expected_type} 코드가 올바르지 않습니다: {code}")

    def _normalize_expertise(
        self, items: list[ProjectExpertiseWrite]
    ) -> list[dict[str, str]]:
        seen: set[str] = set()
        result: list[dict[str, str]] = []
        for item in items:
            code = item.exp_code.strip()
            if not code:
                raise ValidationAppError("전문분야 코드가 비어 있습니다.")
            if code in seen:
                raise ValidationAppError(f"전문분야 코드가 중복되었습니다: {code}")
            seen.add(code)
            if item.evidence_type not in ALLOWED_EVIDENCE:
                raise ValidationAppError("evidence_type이 올바르지 않습니다.")
            result.append({"exp_code": code, "evidence_type": item.evidence_type})
        self._validate_codes(
            [i["exp_code"] for i in result], "EXP", InvalidProjectExpCodeError
        )
        return result

    def _to_detail(self, project: Project) -> ProjectDetail:
        rel = self.repo.relation_codes(project.id)
        return ProjectDetail(
            id=project.id,
            person_id=project.person_id,
            project_name=project.project_name,
            customer_name=project.customer_name,
            start_date=project.start_date,
            end_date=project.end_date,
            duration_months=project.duration_months,
            responsibilities=project.responsibilities,
            project_summary=project.project_summary,
            source_type=project.source_type,
            source_analysis_run_id=project.source_analysis_run_id,
            jobs=[CodeRef(code=c, name=n or c) for c, n in rel["jobs"]],
            skills=[CodeRef(code=c, name=n or c) for c, n in rel["skills"]],
            expertise=[
                ProjectExpertiseItem(
                    code=c, name=n or c, evidence_type=ev  # type: ignore[arg-type]
                )
                for c, n, ev in rel["expertise"]
            ],
            business_domains=[
                CodeRef(code=c, name=n or c) for c, n in rel["business_domains"]
            ],
            customer_types=[
                CodeRef(code=c, name=n or c) for c, n in rel["customer_types"]
            ],
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    def _audit_slice(self, project: Project) -> dict:
        detail = self._to_detail(project)
        return {
            "id": str(detail.id),
            "person_id": str(detail.person_id),
            "project_name": detail.project_name,
            "customer_name": detail.customer_name,
            "start_date": detail.start_date.isoformat() if detail.start_date else None,
            "end_date": detail.end_date.isoformat() if detail.end_date else None,
            "duration_months": detail.duration_months,
            "responsibilities": detail.responsibilities,
            "project_summary": detail.project_summary,
            "source_type": detail.source_type,
            "source_analysis_run_id": (
                str(detail.source_analysis_run_id)
                if detail.source_analysis_run_id
                else None
            ),
            "job_codes": [j.code for j in detail.jobs],
            "tech_codes": [s.code for s in detail.skills],
            "exp_codes": [e.code for e in detail.expertise],
            "biz_codes": [b.code for b in detail.business_domains],
            "customer_type_codes": [c.code for c in detail.customer_types],
        }

    def list_projects(
        self,
        person_id: UUID,
        *,
        job_codes: str | None = None,
        tech_codes: str | None = None,
        exp_codes: str | None = None,
        biz_codes: str | None = None,
        customer_type_codes: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
        is_admin: bool = False,
    ) -> tuple[list[ProjectDetail], PageMeta]:
        self._require_person_profile(person_id, is_admin=is_admin)
        if page < 1:
            raise ValidationAppError("page는 1 이상이어야 합니다.")
        if page_size < 1 or page_size > 100:
            raise ValidationAppError("page_size는 1~100 사이여야 합니다.")
        try:
            rows, total = self.repo.list_projects(
                person_id,
                job_codes=job_codes,
                tech_codes=tech_codes,
                exp_codes=exp_codes,
                biz_codes=biz_codes,
                customer_type_codes=customer_type_codes,
                date_from=date_from,
                date_to=date_to,
                page=page,
                page_size=page_size,
            )
        except ValueError as exc:
            raise ValidationAppError(str(exc)) from exc
        items = [self._to_detail(row) for row in rows]
        meta = PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if total else 0,
        )
        return items, meta

    def get_project(self, project_id: UUID, *, is_admin: bool = False) -> ProjectDetail:
        project = self._require_project(project_id)
        person = self.people_repo.get_person(project.person_id)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        ensure_person_readable(person, is_admin=is_admin)
        return self._to_detail(project)

    def create_project(
        self, person_id: UUID, payload: ProjectCreateRequest, actor_user_id: UUID
    ) -> ProjectDetail:
        person, profile = self._require_person_profile(person_id, for_update=True)

        job_codes = self._dedupe_codes(payload.job_codes, label="직무")
        tech_codes = self._dedupe_codes(payload.tech_codes, label="기술")
        biz_codes = self._dedupe_codes(payload.biz_codes, label="사업분야")
        customer_codes = self._dedupe_codes(
            payload.customer_type_codes, label="고객유형"
        )
        expertise = self._normalize_expertise(payload.expertise)

        self._validate_codes(job_codes, "JOB", InvalidProjectJobCodeError)
        self._validate_codes(tech_codes, "TECH", InvalidProjectTechCodeError)
        self._validate_codes(biz_codes, "BIZ", InvalidProjectBizCodeError)
        self._validate_codes(
            customer_codes, "CUSTOMER_TYPE", InvalidProjectCustomerTypeCodeError
        )

        project = self.repo.create_project(
            person_id,
            project_name=payload.project_name,
            customer_name=payload.customer_name,
            start_date=payload.start_date,
            end_date=payload.end_date,
            duration_months=payload.duration_months,
            responsibilities=payload.responsibilities,
            project_summary=payload.project_summary,
            source_type="USER",
            source_analysis_run_id=None,
        )
        self.repo.replace_jobs(project.id, job_codes)
        self.repo.replace_skills(project.id, tech_codes)
        self.repo.replace_expertise(project.id, expertise)
        self.repo.replace_biz(project.id, biz_codes)
        self.repo.replace_customer_types(project.id, customer_codes)

        after = self._audit_slice(project)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="PROJECT_CREATE",
            target_type="PROJECT",
            target_id=project.id,
            after=after,
        )
        self.db.commit()
        self.db.refresh(project)
        return self._to_detail(project)

    def update_project(
        self, project_id: UUID, payload: ProjectUpdateRequest, actor_user_id: UUID
    ) -> ProjectDetail:
        project = self._require_project(project_id, for_update=True)
        person, profile = self._require_person_profile(
            project.person_id, for_update=True
        )
        before = self._audit_slice(project)
        fields_set = payload.model_fields_set

        scalar_map = {
            "project_name": "project_name",
            "customer_name": "customer_name",
            "start_date": "start_date",
            "end_date": "end_date",
            "duration_months": "duration_months",
            "responsibilities": "responsibilities",
            "project_summary": "project_summary",
        }
        for key, attr in scalar_map.items():
            if key in fields_set:
                setattr(project, attr, getattr(payload, key))

        # Validate merged dates against persisted values.
        start = project.start_date
        end = project.end_date
        if start is not None and end is not None and end < start:
            raise ValidationAppError("종료일은 시작일보다 빠를 수 없습니다.")

        if "job_codes" in fields_set:
            codes = self._dedupe_codes(payload.job_codes or [], label="직무")
            self._validate_codes(codes, "JOB", InvalidProjectJobCodeError)
            self.repo.replace_jobs(project.id, codes)
        if "tech_codes" in fields_set:
            codes = self._dedupe_codes(payload.tech_codes or [], label="기술")
            self._validate_codes(codes, "TECH", InvalidProjectTechCodeError)
            self.repo.replace_skills(project.id, codes)
        if "expertise" in fields_set:
            items = self._normalize_expertise(payload.expertise or [])
            self.repo.replace_expertise(project.id, items)
        if "biz_codes" in fields_set:
            codes = self._dedupe_codes(payload.biz_codes or [], label="사업분야")
            self._validate_codes(codes, "BIZ", InvalidProjectBizCodeError)
            self.repo.replace_biz(project.id, codes)
        if "customer_type_codes" in fields_set:
            codes = self._dedupe_codes(
                payload.customer_type_codes or [], label="고객유형"
            )
            self._validate_codes(
                codes, "CUSTOMER_TYPE", InvalidProjectCustomerTypeCodeError
            )
            self.repo.replace_customer_types(project.id, codes)

        # Manual edit → USER; keep original source_analysis_run_id provenance.
        project.source_type = "USER"
        self.repo.touch(project)

        after = self._audit_slice(project)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="PROJECT_UPDATE",
            target_type="PROJECT",
            target_id=project.id,
            before=before,
            after=after,
        )
        self.db.commit()
        self.db.refresh(project)
        return self._to_detail(project)

    def delete_project(self, project_id: UUID, actor_user_id: UUID) -> None:
        project = self._require_project(project_id, for_update=True)
        person, profile = self._require_person_profile(
            project.person_id, for_update=True
        )
        before = self._audit_slice(project)
        self.repo.soft_delete(project)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="PROJECT_DELETE",
            target_type="PROJECT",
            target_id=project.id,
            before=before,
        )
        self.db.commit()
