"""Career HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_authenticated_user,
    require_csrf,
)
from app.modules.career.schemas import (
    CertificationCreateRequest,
    CertificationDetailResponse,
    CertificationListResponse,
    CertificationUpdateRequest,
    EducationCreateRequest,
    EducationDetailResponse,
    EducationListResponse,
    EducationUpdateRequest,
    EmploymentCreateRequest,
    EmploymentDetailResponse,
    EmploymentListResponse,
    EmploymentUpdateRequest,
)
from app.modules.career.service import CareerService

person_career_router = APIRouter(prefix="/people", tags=["career"])
employment_router = APIRouter(prefix="/employment-history", tags=["career"])
education_router = APIRouter(prefix="/education", tags=["career"])
certifications_router = APIRouter(prefix="/certifications", tags=["career"])


def get_career_service(db: Session = Depends(get_db)) -> CareerService:
    return CareerService(db)


# --- Employment ---


@person_career_router.get(
    "/{person_id}/employment-history", response_model=EmploymentListResponse
)
def list_employment(
    person_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: CareerService = Depends(get_career_service),
) -> EmploymentListResponse:
    return EmploymentListResponse(
        data=service.list_employment(person_id, is_admin=ctx.user.role == "ADMIN")
    )


@person_career_router.post(
    "/{person_id}/employment-history",
    response_model=EmploymentDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employment(
    person_id: UUID,
    payload: EmploymentCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> EmploymentDetailResponse:
    return EmploymentDetailResponse(
        data=service.create_employment(person_id, payload, ctx.user.id)
    )


@employment_router.patch("/{employment_id}", response_model=EmploymentDetailResponse)
def update_employment(
    employment_id: UUID,
    payload: EmploymentUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> EmploymentDetailResponse:
    return EmploymentDetailResponse(
        data=service.update_employment(employment_id, payload, ctx.user.id)
    )


@employment_router.delete("/{employment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employment(
    employment_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> Response:
    service.delete_employment(employment_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Education ---


@person_career_router.get("/{person_id}/education", response_model=EducationListResponse)
def list_education(
    person_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: CareerService = Depends(get_career_service),
) -> EducationListResponse:
    return EducationListResponse(
        data=service.list_education(person_id, is_admin=ctx.user.role == "ADMIN")
    )


@person_career_router.post(
    "/{person_id}/education",
    response_model=EducationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_education(
    person_id: UUID,
    payload: EducationCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> EducationDetailResponse:
    return EducationDetailResponse(
        data=service.create_education(person_id, payload, ctx.user.id)
    )


@education_router.patch("/{education_id}", response_model=EducationDetailResponse)
def update_education(
    education_id: UUID,
    payload: EducationUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> EducationDetailResponse:
    return EducationDetailResponse(
        data=service.update_education(education_id, payload, ctx.user.id)
    )


@education_router.delete("/{education_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_education(
    education_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> Response:
    service.delete_education(education_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Certifications ---


@person_career_router.get(
    "/{person_id}/certifications", response_model=CertificationListResponse
)
def list_certifications(
    person_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: CareerService = Depends(get_career_service),
) -> CertificationListResponse:
    return CertificationListResponse(
        data=service.list_certifications(person_id, is_admin=ctx.user.role == "ADMIN")
    )


@person_career_router.post(
    "/{person_id}/certifications",
    response_model=CertificationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_certification(
    person_id: UUID,
    payload: CertificationCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> CertificationDetailResponse:
    return CertificationDetailResponse(
        data=service.create_certification(person_id, payload, ctx.user.id)
    )


@certifications_router.patch(
    "/{certification_id}", response_model=CertificationDetailResponse
)
def update_certification(
    certification_id: UUID,
    payload: CertificationUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> CertificationDetailResponse:
    return CertificationDetailResponse(
        data=service.update_certification(certification_id, payload, ctx.user.id)
    )


@certifications_router.delete(
    "/{certification_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_certification(
    certification_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: CareerService = Depends(get_career_service),
) -> Response:
    service.delete_certification(certification_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
