"""Project DB access."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.code import CodeMaster
from app.db.models.project import (
    Project,
    ProjectBusinessDomain,
    ProjectCustomerType,
    ProjectExpertise,
    ProjectJob,
    ProjectSkill,
)


def _parse_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_year_month(raw: str | None, *, end: bool = False) -> date | None:
    """Parse ``YYYY-MM`` or ``YYYY-MM-DD`` into a date bound."""
    import calendar

    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) == 7 and text[4] == "-":
        year = int(text[:4])
        month = int(text[5:7])
        if end:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, last_day)
        return date(year, month, 1)
    return date.fromisoformat(text)


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_project(
        self, project_id: UUID, *, for_update: bool = False, include_deleted: bool = False
    ) -> Project | None:
        stmt = select(Project).where(Project.id == project_id)
        if not include_deleted:
            stmt = stmt.where(Project.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_codes_map(self, codes: list[str]) -> dict[str, CodeMaster]:
        if not codes:
            return {}
        rows = self.db.execute(
            select(CodeMaster).where(CodeMaster.code.in_(codes))
        ).scalars().all()
        return {row.code: row for row in rows}

    def create_project(self, person_id: UUID, **fields: Any) -> Project:
        project = Project(person_id=person_id, **fields)
        self.db.add(project)
        self.db.flush()
        return project

    def replace_jobs(self, project_id: UUID, codes: list[str]) -> None:
        self.db.execute(delete(ProjectJob).where(ProjectJob.project_id == project_id))
        for code in codes:
            self.db.add(ProjectJob(project_id=project_id, job_code=code))
        self.db.flush()

    def replace_skills(self, project_id: UUID, codes: list[str]) -> None:
        self.db.execute(delete(ProjectSkill).where(ProjectSkill.project_id == project_id))
        for code in codes:
            self.db.add(ProjectSkill(project_id=project_id, tech_code=code))
        self.db.flush()

    def replace_expertise(
        self, project_id: UUID, items: list[dict[str, str]]
    ) -> None:
        self.db.execute(
            delete(ProjectExpertise).where(ProjectExpertise.project_id == project_id)
        )
        for item in items:
            self.db.add(
                ProjectExpertise(
                    project_id=project_id,
                    exp_code=item["exp_code"],
                    evidence_type=item.get("evidence_type", "EXPLICIT"),
                )
            )
        self.db.flush()

    def replace_biz(self, project_id: UUID, codes: list[str]) -> None:
        self.db.execute(
            delete(ProjectBusinessDomain).where(
                ProjectBusinessDomain.project_id == project_id
            )
        )
        for code in codes:
            self.db.add(ProjectBusinessDomain(project_id=project_id, biz_code=code))
        self.db.flush()

    def replace_customer_types(self, project_id: UUID, codes: list[str]) -> None:
        self.db.execute(
            delete(ProjectCustomerType).where(
                ProjectCustomerType.project_id == project_id
            )
        )
        for code in codes:
            self.db.add(
                ProjectCustomerType(project_id=project_id, customer_type_code=code)
            )
        self.db.flush()

    def soft_delete(self, project: Project) -> None:
        now = datetime.now(UTC)
        project.deleted_at = now
        project.updated_at = now
        self.db.add(project)
        self.db.flush()

    def touch(self, project: Project) -> None:
        project.updated_at = datetime.now(UTC)
        self.db.add(project)

    def relation_codes(self, project_id: UUID) -> dict[str, list[Any]]:
        jobs = list(
            self.db.execute(
                select(ProjectJob.job_code, CodeMaster.name)
                .outerjoin(CodeMaster, CodeMaster.code == ProjectJob.job_code)
                .where(ProjectJob.project_id == project_id)
                .order_by(ProjectJob.job_code.asc())
            ).all()
        )
        skills = list(
            self.db.execute(
                select(ProjectSkill.tech_code, CodeMaster.name)
                .outerjoin(CodeMaster, CodeMaster.code == ProjectSkill.tech_code)
                .where(ProjectSkill.project_id == project_id)
                .order_by(ProjectSkill.tech_code.asc())
            ).all()
        )
        expertise = list(
            self.db.execute(
                select(
                    ProjectExpertise.exp_code,
                    CodeMaster.name,
                    ProjectExpertise.evidence_type,
                )
                .outerjoin(CodeMaster, CodeMaster.code == ProjectExpertise.exp_code)
                .where(ProjectExpertise.project_id == project_id)
                .order_by(ProjectExpertise.exp_code.asc())
            ).all()
        )
        biz = list(
            self.db.execute(
                select(ProjectBusinessDomain.biz_code, CodeMaster.name)
                .outerjoin(
                    CodeMaster, CodeMaster.code == ProjectBusinessDomain.biz_code
                )
                .where(ProjectBusinessDomain.project_id == project_id)
                .order_by(ProjectBusinessDomain.biz_code.asc())
            ).all()
        )
        customers = list(
            self.db.execute(
                select(ProjectCustomerType.customer_type_code, CodeMaster.name)
                .outerjoin(
                    CodeMaster,
                    CodeMaster.code == ProjectCustomerType.customer_type_code,
                )
                .where(ProjectCustomerType.project_id == project_id)
                .order_by(ProjectCustomerType.customer_type_code.asc())
            ).all()
        )
        return {
            "jobs": jobs,
            "skills": skills,
            "expertise": expertise,
            "business_domains": biz,
            "customer_types": customers,
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
    ) -> tuple[list[Project], int]:
        stmt = select(Project).where(
            Project.person_id == person_id, Project.deleted_at.is_(None)
        )

        jobs = _parse_codes(job_codes)
        techs = _parse_codes(tech_codes)
        exps = _parse_codes(exp_codes)
        bizs = _parse_codes(biz_codes)
        customers = _parse_codes(customer_type_codes)

        if jobs:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectJob.project_id).where(ProjectJob.job_code.in_(jobs))
                )
            )
        if techs:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectSkill.project_id).where(
                        ProjectSkill.tech_code.in_(techs)
                    )
                )
            )
        if exps:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectExpertise.project_id).where(
                        ProjectExpertise.exp_code.in_(exps)
                    )
                )
            )
        if bizs:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectBusinessDomain.project_id).where(
                        ProjectBusinessDomain.biz_code.in_(bizs)
                    )
                )
            )
        if customers:
            stmt = stmt.where(
                Project.id.in_(
                    select(ProjectCustomerType.project_id).where(
                        ProjectCustomerType.customer_type_code.in_(customers)
                    )
                )
            )

        # Period overlap. NULL project dates are treated as open-ended on that side.
        # Both NULL → always overlaps (included whenever a date filter is set).
        filter_from = _parse_year_month(date_from, end=False)
        filter_to = _parse_year_month(date_to, end=True)
        if filter_from is not None:
            stmt = stmt.where(
                or_(Project.end_date.is_(None), Project.end_date >= filter_from)
            )
        if filter_to is not None:
            stmt = stmt.where(
                or_(Project.start_date.is_(None), Project.start_date <= filter_to)
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int(self.db.execute(count_stmt).scalar_one())

        rows = list(
            self.db.execute(
                stmt.order_by(
                    Project.start_date.desc().nullslast(), Project.created_at.desc()
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()
        )
        return rows, total
