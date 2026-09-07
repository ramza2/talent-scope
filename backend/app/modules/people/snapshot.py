"""Confirmed profile snapshot builder for revisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.code import CodeMaster
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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def build_confirmed_profile_snapshot(db: Session, person_id: UUID) -> dict[str, Any]:
    """Build a deterministic Confirmed Profile snapshot.

    Includes profile, person-level JOB/TECH/EXP sets, employment history,
    projects (with relations), education, and certifications.
    Soft-deleted projects are excluded.
    """
    profile = db.execute(
        select(PersonProfile).where(PersonProfile.person_id == person_id)
    ).scalar_one()

    jobs = list(
        db.execute(
            select(PersonJob, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonJob.job_code)
            .where(PersonJob.person_id == person_id, PersonJob.is_active.is_(True))
            .order_by(PersonJob.sort_order.asc(), PersonJob.job_code.asc())
        ).all()
    )
    skills = list(
        db.execute(
            select(PersonSkill, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonSkill.tech_code)
            .where(PersonSkill.person_id == person_id)
            .order_by(
                PersonSkill.is_representative.desc(),
                PersonSkill.tech_code.asc(),
            )
        ).all()
    )
    expertise = list(
        db.execute(
            select(PersonExpertise, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == PersonExpertise.exp_code)
            .where(PersonExpertise.person_id == person_id)
            .order_by(PersonExpertise.exp_code.asc())
        ).all()
    )

    employment_rows = list(
        db.execute(
            select(EmploymentHistory)
            .where(EmploymentHistory.person_id == person_id)
            .order_by(
                EmploymentHistory.start_date.desc().nullslast(),
                EmploymentHistory.created_at.desc(),
            )
        ).scalars().all()
    )

    projects = list(
        db.execute(
            select(Project)
            .where(Project.person_id == person_id, Project.deleted_at.is_(None))
            .order_by(Project.start_date.desc().nullslast(), Project.created_at.desc())
        ).scalars().all()
    )
    project_ids = [p.id for p in projects]

    project_jobs: dict[UUID, list[tuple[str, str | None]]] = {pid: [] for pid in project_ids}
    project_skills: dict[UUID, list[tuple[str, str | None]]] = {pid: [] for pid in project_ids}
    project_exp: dict[UUID, list[tuple[str, str | None, str]]] = {
        pid: [] for pid in project_ids
    }
    project_biz: dict[UUID, list[tuple[str, str | None]]] = {pid: [] for pid in project_ids}
    project_customers: dict[UUID, list[tuple[str, str | None]]] = {
        pid: [] for pid in project_ids
    }

    if project_ids:
        for row, name in db.execute(
            select(ProjectJob, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == ProjectJob.job_code)
            .where(ProjectJob.project_id.in_(project_ids))
            .order_by(ProjectJob.job_code.asc())
        ).all():
            project_jobs.setdefault(row.project_id, []).append((row.job_code, name))

        for row, name in db.execute(
            select(ProjectSkill, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == ProjectSkill.tech_code)
            .where(ProjectSkill.project_id.in_(project_ids))
            .order_by(ProjectSkill.tech_code.asc())
        ).all():
            project_skills.setdefault(row.project_id, []).append((row.tech_code, name))

        for row, name in db.execute(
            select(ProjectExpertise, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == ProjectExpertise.exp_code)
            .where(ProjectExpertise.project_id.in_(project_ids))
            .order_by(ProjectExpertise.exp_code.asc())
        ).all():
            project_exp.setdefault(row.project_id, []).append(
                (row.exp_code, name, row.evidence_type)
            )

        for row, name in db.execute(
            select(ProjectBusinessDomain, CodeMaster.name)
            .outerjoin(CodeMaster, CodeMaster.code == ProjectBusinessDomain.biz_code)
            .where(ProjectBusinessDomain.project_id.in_(project_ids))
            .order_by(ProjectBusinessDomain.biz_code.asc())
        ).all():
            project_biz.setdefault(row.project_id, []).append((row.biz_code, name))

        for row, name in db.execute(
            select(ProjectCustomerType, CodeMaster.name)
            .outerjoin(
                CodeMaster, CodeMaster.code == ProjectCustomerType.customer_type_code
            )
            .where(ProjectCustomerType.project_id.in_(project_ids))
            .order_by(ProjectCustomerType.customer_type_code.asc())
        ).all():
            project_customers.setdefault(row.project_id, []).append(
                (row.customer_type_code, name)
            )

    education_rows = list(
        db.execute(
            select(Education)
            .where(Education.person_id == person_id)
            .order_by(Education.start_date.desc().nullslast(), Education.created_at.desc())
        ).scalars().all()
    )

    certification_rows = list(
        db.execute(
            select(Certification)
            .where(Certification.person_id == person_id)
            .order_by(
                Certification.acquired_date.desc().nullslast(),
                Certification.created_at.desc(),
            )
        ).scalars().all()
    )

    return {
        "profile": {
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
            "career_start_date": _iso(profile.career_start_date),
            "career_calculated_months": profile.career_calculated_months,
            "career_document_value": profile.career_document_value,
            "career_confirmed_months": profile.career_confirmed_months,
            "profile_summary": profile.profile_summary,
            "profile_version": profile.profile_version,
        },
        "jobs": [
            {
                "job_code": row.job_code,
                "name": name,
                "job_type": row.job_type,
                "sort_order": row.sort_order,
                "source_type": row.source_type,
            }
            for row, name in jobs
        ],
        "skills": [
            {
                "tech_code": row.tech_code,
                "name": name,
                "last_used_year": row.last_used_year,
                "experience_months": row.experience_months,
                "is_representative": row.is_representative,
                "source_type": row.source_type,
            }
            for row, name in skills
        ],
        "expertise": [
            {
                "exp_code": row.exp_code,
                "name": name,
                "evidence_type": row.evidence_type,
                "source_type": row.source_type,
            }
            for row, name in expertise
        ],
        "employment_history": [
            {
                "id": str(row.id),
                "company_name": row.company_name,
                "department": row.department,
                "title": row.title,
                "start_date": _iso(row.start_date),
                "end_date": _iso(row.end_date),
                "responsibilities": row.responsibilities,
                "source_type": row.source_type,
            }
            for row in employment_rows
        ],
        "projects": [
            {
                "id": str(project.id),
                "project_name": project.project_name,
                "customer_name": project.customer_name,
                "start_date": _iso(project.start_date),
                "end_date": _iso(project.end_date),
                "duration_months": project.duration_months,
                "responsibilities": project.responsibilities,
                "project_summary": project.project_summary,
                "source_type": project.source_type,
                "jobs": [
                    {"code": code, "name": name or code}
                    for code, name in project_jobs.get(project.id, [])
                ],
                "skills": [
                    {"code": code, "name": name or code}
                    for code, name in project_skills.get(project.id, [])
                ],
                "expertise": [
                    {
                        "code": code,
                        "name": name or code,
                        "evidence_type": evidence_type,
                    }
                    for code, name, evidence_type in project_exp.get(project.id, [])
                ],
                "business_domains": [
                    {"code": code, "name": name or code}
                    for code, name in project_biz.get(project.id, [])
                ],
                "customer_types": [
                    {"code": code, "name": name or code}
                    for code, name in project_customers.get(project.id, [])
                ],
            }
            for project in projects
        ],
        "education": [
            {
                "id": str(row.id),
                "school_name": row.school_name,
                "major": row.major,
                "degree": row.degree,
                "start_date": _iso(row.start_date),
                "end_date": _iso(row.end_date),
                "status": row.status,
                "source_type": row.source_type,
            }
            for row in education_rows
        ],
        "certifications": [
            {
                "id": str(row.id),
                "certification_name": row.certification_name,
                "issuer": row.issuer,
                "acquired_date": _iso(row.acquired_date),
                "expiry_date": _iso(row.expiry_date),
                "source_type": row.source_type,
            }
            for row in certification_rows
        ],
    }
