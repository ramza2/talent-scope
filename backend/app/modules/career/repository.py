"""Career DB access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models.person import Certification, Education, EmploymentHistory


class CareerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Employment ---

    def list_employment(self, person_id: UUID) -> list[EmploymentHistory]:
        return list(
            self.db.execute(
                select(EmploymentHistory)
                .where(EmploymentHistory.person_id == person_id)
                .order_by(
                    EmploymentHistory.start_date.desc().nullslast(),
                    EmploymentHistory.created_at.desc(),
                )
            ).scalars().all()
        )

    def get_employment(
        self, employment_id: UUID, *, for_update: bool = False
    ) -> EmploymentHistory | None:
        stmt = select(EmploymentHistory).where(EmploymentHistory.id == employment_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def create_employment(self, person_id: UUID, **fields: Any) -> EmploymentHistory:
        row = EmploymentHistory(person_id=person_id, **fields)
        self.db.add(row)
        self.db.flush()
        return row

    def delete_employment(self, row: EmploymentHistory) -> None:
        self.db.execute(
            delete(EmploymentHistory).where(EmploymentHistory.id == row.id)
        )
        self.db.flush()

    def touch_employment(self, row: EmploymentHistory) -> None:
        row.updated_at = datetime.now(UTC)
        self.db.add(row)

    # --- Education ---

    def list_education(self, person_id: UUID) -> list[Education]:
        return list(
            self.db.execute(
                select(Education)
                .where(Education.person_id == person_id)
                .order_by(
                    Education.start_date.desc().nullslast(), Education.created_at.desc()
                )
            ).scalars().all()
        )

    def get_education(
        self, education_id: UUID, *, for_update: bool = False
    ) -> Education | None:
        stmt = select(Education).where(Education.id == education_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def create_education(self, person_id: UUID, **fields: Any) -> Education:
        row = Education(person_id=person_id, **fields)
        self.db.add(row)
        self.db.flush()
        return row

    def delete_education(self, row: Education) -> None:
        self.db.execute(delete(Education).where(Education.id == row.id))
        self.db.flush()

    def touch_education(self, row: Education) -> None:
        row.updated_at = datetime.now(UTC)
        self.db.add(row)

    # --- Certification ---

    def list_certifications(self, person_id: UUID) -> list[Certification]:
        return list(
            self.db.execute(
                select(Certification)
                .where(Certification.person_id == person_id)
                .order_by(
                    Certification.acquired_date.desc().nullslast(),
                    Certification.created_at.desc(),
                )
            ).scalars().all()
        )

    def get_certification(
        self, certification_id: UUID, *, for_update: bool = False
    ) -> Certification | None:
        stmt = select(Certification).where(Certification.id == certification_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def create_certification(self, person_id: UUID, **fields: Any) -> Certification:
        row = Certification(person_id=person_id, **fields)
        self.db.add(row)
        self.db.flush()
        return row

    def delete_certification(self, row: Certification) -> None:
        self.db.execute(delete(Certification).where(Certification.id == row.id))
        self.db.flush()

    def touch_certification(self, row: Certification) -> None:
        row.updated_at = datetime.now(UTC)
        self.db.add(row)
