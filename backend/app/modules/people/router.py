"""People HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_authenticated_user,
    require_csrf,
)
from app.modules.people.schemas import (
    ExpertiseReplaceRequest,
    JobsReplaceRequest,
    PeopleListResponse,
    PersonCreateRequest,
    PersonDetailResponse,
    PersonStatusUpdateRequest,
    ProfileUpdateRequest,
    RevisionListResponse,
    SkillsReplaceRequest,
)
from app.modules.people.service import PeopleService

router = APIRouter(prefix="/people", tags=["people"])


def get_people_service(db: Session = Depends(get_db)) -> PeopleService:
    return PeopleService(db)


@router.get("", response_model=PeopleListResponse)
def list_people(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    job_codes: str | None = Query(default=None),
    grade: str | None = Query(default=None),
    tech_codes: str | None = Query(default=None),
    exp_codes: str | None = Query(default=None),
    affiliation: str | None = Query(default=None),
    analysis_status: str | None = Query(default=None),
    sort: str = Query(default="updated_desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: PeopleService = Depends(get_people_service),
) -> PeopleListResponse:
    items, meta = service.list_people(
        q=q,
        status=status,
        job_codes=job_codes,
        grade=grade,
        tech_codes=tech_codes,
        exp_codes=exp_codes,
        affiliation=affiliation,
        analysis_status=analysis_status,
        sort=sort,
        page=page,
        page_size=page_size,
        is_admin=ctx.user.role == "ADMIN",
    )
    return PeopleListResponse(data=items, meta=meta)


@router.post("", response_model=PersonDetailResponse, status_code=status.HTTP_201_CREATED)
def create_person(
    payload: PersonCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(data=service.create_person(payload, ctx.user.id))


@router.get("/{person_id}", response_model=PersonDetailResponse)
def get_person(
    person_id: UUID,
    ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.get_detail(person_id, is_admin=ctx.user.role == "ADMIN")
    )


@router.patch("/{person_id}", response_model=PersonDetailResponse)
def update_person_status(
    person_id: UUID,
    payload: PersonStatusUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.update_status(person_id, payload, ctx.user.id)
    )


@router.patch("/{person_id}/profile", response_model=PersonDetailResponse)
def update_person_profile(
    person_id: UUID,
    payload: ProfileUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.update_profile(person_id, payload, ctx.user.id)
    )


@router.put("/{person_id}/jobs", response_model=PersonDetailResponse)
def replace_jobs(
    person_id: UUID,
    payload: JobsReplaceRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.replace_jobs(person_id, payload, ctx.user.id)
    )


@router.put("/{person_id}/skills", response_model=PersonDetailResponse)
def replace_skills(
    person_id: UUID,
    payload: SkillsReplaceRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.replace_skills(person_id, payload, ctx.user.id)
    )


@router.put("/{person_id}/expertise", response_model=PersonDetailResponse)
def replace_expertise(
    person_id: UUID,
    payload: ExpertiseReplaceRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: PeopleService = Depends(get_people_service),
) -> PersonDetailResponse:
    return PersonDetailResponse(
        data=service.replace_expertise(person_id, payload, ctx.user.id)
    )


@router.get("/{person_id}/revisions", response_model=RevisionListResponse)
def list_revisions(
    person_id: UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _admin: AuthenticatedContext = Depends(require_admin),
    service: PeopleService = Depends(get_people_service),
) -> RevisionListResponse:
    items, meta = service.list_revisions(person_id, page=page, page_size=page_size)
    return RevisionListResponse(data=items, meta=meta)
