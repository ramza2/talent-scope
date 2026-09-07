"""Confirmed profile snapshot builder for revisions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.code import CodeMaster
from app.db.models.person import PersonExpertise, PersonJob, PersonProfile, PersonSkill


def build_confirmed_profile_snapshot(db: Session, person_id: UUID) -> dict[str, Any]:
    """Build a reproducible Confirmed Profile snapshot.

    Includes profile + jobs + skills + expertise only.
    Project-derived domains are not part of direct edit targets.
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
            "career_start_date": (
                profile.career_start_date.isoformat() if profile.career_start_date else None
            ),
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
    }
