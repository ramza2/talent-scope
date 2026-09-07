"""Career business logic — employment / education / certification."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationAppError
from app.db.models.person import Certification, Education, EmploymentHistory
from app.modules.career.repository import CareerRepository
from app.modules.career.schemas import (
    CertificationCreateRequest,
    CertificationItem,
    CertificationUpdateRequest,
    EducationCreateRequest,
    EducationItem,
    EducationUpdateRequest,
    EmploymentCreateRequest,
    EmploymentItem,
    EmploymentUpdateRequest,
)
from app.modules.people.finalize import finalize_confirmed_profile_change
from app.modules.people.repository import PeopleRepository


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


class CareerService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = CareerRepository(db)
        self.people_repo = PeopleRepository(db)

    def _require_person_profile(self, person_id: UUID, *, for_update: bool = False):
        person = self.people_repo.get_person(person_id, for_update=for_update)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        profile = self.people_repo.get_profile(person_id, for_update=for_update)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")
        return person, profile

    def _validate_merged_dates(self, start, end, *, label: str = "종료일") -> None:
        if start is not None and end is not None and end < start:
            raise ValidationAppError(f"{label}은 시작일보다 빠를 수 없습니다.")

    # --- Employment ---

    def _employment_item(self, row: EmploymentHistory) -> EmploymentItem:
        return EmploymentItem(
            id=row.id,
            person_id=row.person_id,
            company_name=row.company_name,
            department=row.department,
            title=row.title,
            start_date=row.start_date,
            end_date=row.end_date,
            responsibilities=row.responsibilities,
            source_type=row.source_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _employment_audit(self, row: EmploymentHistory) -> dict:
        return {
            "id": str(row.id),
            "person_id": str(row.person_id),
            "company_name": row.company_name,
            "department": row.department,
            "title": row.title,
            "start_date": _iso(row.start_date),
            "end_date": _iso(row.end_date),
            "responsibilities": row.responsibilities,
            "source_type": row.source_type,
        }

    def list_employment(self, person_id: UUID) -> list[EmploymentItem]:
        self._require_person_profile(person_id)
        return [self._employment_item(r) for r in self.repo.list_employment(person_id)]

    def create_employment(
        self, person_id: UUID, payload: EmploymentCreateRequest, actor_user_id: UUID
    ) -> EmploymentItem:
        person, profile = self._require_person_profile(person_id, for_update=True)
        # Capture career months before mutation to assert we do not auto-recalc.
        _ = profile.career_calculated_months
        row = self.repo.create_employment(
            person_id,
            company_name=payload.company_name,
            department=payload.department,
            title=payload.title,
            start_date=payload.start_date,
            end_date=payload.end_date,
            responsibilities=payload.responsibilities,
            source_type="USER",
        )
        after = self._employment_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EMPLOYMENT_CREATE",
            target_type="EMPLOYMENT_HISTORY",
            target_id=row.id,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._employment_item(row)

    def update_employment(
        self,
        employment_id: UUID,
        payload: EmploymentUpdateRequest,
        actor_user_id: UUID,
    ) -> EmploymentItem:
        row = self.repo.get_employment(employment_id, for_update=True)
        if row is None:
            raise NotFoundError("근무경력을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._employment_audit(row)
        fields_set = payload.model_fields_set
        for key in (
            "company_name",
            "department",
            "title",
            "start_date",
            "end_date",
            "responsibilities",
        ):
            if key in fields_set:
                setattr(row, key, getattr(payload, key))
        self._validate_merged_dates(row.start_date, row.end_date)
        row.source_type = "USER"
        self.repo.touch_employment(row)
        after = self._employment_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EMPLOYMENT_UPDATE",
            target_type="EMPLOYMENT_HISTORY",
            target_id=row.id,
            before=before,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._employment_item(row)

    def delete_employment(self, employment_id: UUID, actor_user_id: UUID) -> None:
        row = self.repo.get_employment(employment_id, for_update=True)
        if row is None:
            raise NotFoundError("근무경력을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._employment_audit(row)
        target_id = row.id
        self.repo.delete_employment(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EMPLOYMENT_DELETE",
            target_type="EMPLOYMENT_HISTORY",
            target_id=target_id,
            before=before,
        )
        self.db.commit()

    # --- Education ---

    def _education_item(self, row: Education) -> EducationItem:
        return EducationItem(
            id=row.id,
            person_id=row.person_id,
            school_name=row.school_name,
            major=row.major,
            degree=row.degree,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            source_type=row.source_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _education_audit(self, row: Education) -> dict:
        return {
            "id": str(row.id),
            "person_id": str(row.person_id),
            "school_name": row.school_name,
            "major": row.major,
            "degree": row.degree,
            "start_date": _iso(row.start_date),
            "end_date": _iso(row.end_date),
            "status": row.status,
            "source_type": row.source_type,
        }

    def list_education(self, person_id: UUID) -> list[EducationItem]:
        self._require_person_profile(person_id)
        return [self._education_item(r) for r in self.repo.list_education(person_id)]

    def create_education(
        self, person_id: UUID, payload: EducationCreateRequest, actor_user_id: UUID
    ) -> EducationItem:
        person, profile = self._require_person_profile(person_id, for_update=True)
        row = self.repo.create_education(
            person_id,
            school_name=payload.school_name,
            major=payload.major,
            degree=payload.degree,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status=payload.status,
            source_type="USER",
        )
        after = self._education_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EDUCATION_CREATE",
            target_type="EDUCATION",
            target_id=row.id,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._education_item(row)

    def update_education(
        self, education_id: UUID, payload: EducationUpdateRequest, actor_user_id: UUID
    ) -> EducationItem:
        row = self.repo.get_education(education_id, for_update=True)
        if row is None:
            raise NotFoundError("학력을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._education_audit(row)
        fields_set = payload.model_fields_set
        for key in (
            "school_name",
            "major",
            "degree",
            "start_date",
            "end_date",
            "status",
        ):
            if key in fields_set:
                setattr(row, key, getattr(payload, key))
        self._validate_merged_dates(row.start_date, row.end_date)
        row.source_type = "USER"
        self.repo.touch_education(row)
        after = self._education_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EDUCATION_UPDATE",
            target_type="EDUCATION",
            target_id=row.id,
            before=before,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._education_item(row)

    def delete_education(self, education_id: UUID, actor_user_id: UUID) -> None:
        row = self.repo.get_education(education_id, for_update=True)
        if row is None:
            raise NotFoundError("학력을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._education_audit(row)
        target_id = row.id
        self.repo.delete_education(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="EDUCATION_DELETE",
            target_type="EDUCATION",
            target_id=target_id,
            before=before,
        )
        self.db.commit()

    # --- Certification ---

    def _certification_item(self, row: Certification) -> CertificationItem:
        return CertificationItem(
            id=row.id,
            person_id=row.person_id,
            certification_name=row.certification_name,
            issuer=row.issuer,
            acquired_date=row.acquired_date,
            expiry_date=row.expiry_date,
            certificate_no=row.certificate_no,
            source_type=row.source_type,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _certification_audit(self, row: Certification) -> dict:
        # certificate_no intentionally omitted from audit snapshots.
        return {
            "id": str(row.id),
            "person_id": str(row.person_id),
            "certification_name": row.certification_name,
            "issuer": row.issuer,
            "acquired_date": _iso(row.acquired_date),
            "expiry_date": _iso(row.expiry_date),
            "source_type": row.source_type,
        }

    def list_certifications(self, person_id: UUID) -> list[CertificationItem]:
        self._require_person_profile(person_id)
        return [
            self._certification_item(r) for r in self.repo.list_certifications(person_id)
        ]

    def create_certification(
        self,
        person_id: UUID,
        payload: CertificationCreateRequest,
        actor_user_id: UUID,
    ) -> CertificationItem:
        person, profile = self._require_person_profile(person_id, for_update=True)
        row = self.repo.create_certification(
            person_id,
            certification_name=payload.certification_name,
            issuer=payload.issuer,
            acquired_date=payload.acquired_date,
            expiry_date=payload.expiry_date,
            certificate_no=payload.certificate_no,
            source_type="USER",
        )
        after = self._certification_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="CERTIFICATION_CREATE",
            target_type="CERTIFICATION",
            target_id=row.id,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._certification_item(row)

    def update_certification(
        self,
        certification_id: UUID,
        payload: CertificationUpdateRequest,
        actor_user_id: UUID,
    ) -> CertificationItem:
        row = self.repo.get_certification(certification_id, for_update=True)
        if row is None:
            raise NotFoundError("자격을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._certification_audit(row)
        fields_set = payload.model_fields_set
        for key in (
            "certification_name",
            "issuer",
            "acquired_date",
            "expiry_date",
            "certificate_no",
        ):
            if key in fields_set:
                setattr(row, key, getattr(payload, key))
        if (
            row.acquired_date is not None
            and row.expiry_date is not None
            and row.expiry_date < row.acquired_date
        ):
            raise ValidationAppError("만료일은 취득일보다 빠를 수 없습니다.")
        row.source_type = "USER"
        self.repo.touch_certification(row)
        after = self._certification_audit(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="CERTIFICATION_UPDATE",
            target_type="CERTIFICATION",
            target_id=row.id,
            before=before,
            after=after,
        )
        self.db.commit()
        self.db.refresh(row)
        return self._certification_item(row)

    def delete_certification(
        self, certification_id: UUID, actor_user_id: UUID
    ) -> None:
        row = self.repo.get_certification(certification_id, for_update=True)
        if row is None:
            raise NotFoundError("자격을 찾을 수 없습니다.")
        person, profile = self._require_person_profile(row.person_id, for_update=True)
        before = self._certification_audit(row)
        target_id = row.id
        self.repo.delete_certification(row)
        finalize_confirmed_profile_change(
            self.people_repo,
            person=person,
            profile=profile,
            actor_user_id=actor_user_id,
            action_type="CERTIFICATION_DELETE",
            target_type="CERTIFICATION",
            target_id=target_id,
            before=before,
        )
        self.db.commit()
