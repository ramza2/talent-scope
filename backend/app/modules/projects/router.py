"""Project HTTP endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import (
    AuthenticatedContext,
    require_admin,
    require_authenticated_user,
    require_csrf,
)
from app.modules.projects.schemas import (
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectUpdateRequest,
)
from app.modules.projects.service import ProjectService

person_projects_router = APIRouter(prefix="/people", tags=["projects"])
projects_router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(db)


@person_projects_router.get(
    "/{person_id}/projects", response_model=ProjectListResponse
)
def list_person_projects(
    person_id: UUID,
    job_codes: str | None = Query(default=None),
    tech_codes: str | None = Query(default=None),
    exp_codes: str | None = Query(default=None),
    biz_codes: str | None = Query(default=None),
    customer_type_codes: str | None = Query(default=None),
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectListResponse:
    items, meta = service.list_projects(
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
    return ProjectListResponse(data=items, meta=meta)


@person_projects_router.post(
    "/{person_id}/projects",
    response_model=ProjectDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_person_project(
    person_id: UUID,
    payload: ProjectCreateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: ProjectService = Depends(get_project_service),
) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        data=service.create_project(person_id, payload, ctx.user.id)
    )


@projects_router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: UUID,
    _ctx: AuthenticatedContext = Depends(require_authenticated_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectDetailResponse:
    return ProjectDetailResponse(data=service.get_project(project_id))


@projects_router.patch("/{project_id}", response_model=ProjectDetailResponse)
def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: ProjectService = Depends(get_project_service),
) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        data=service.update_project(project_id, payload, ctx.user.id)
    )


@projects_router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: UUID,
    _admin: AuthenticatedContext = Depends(require_admin),
    ctx: AuthenticatedContext = Depends(require_csrf),
    service: ProjectService = Depends(get_project_service),
) -> Response:
    service.delete_project(project_id, ctx.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
