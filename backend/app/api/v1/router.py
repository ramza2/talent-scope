"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import health
from app.modules.auth import router as auth_router
from app.modules.career import (
    certifications_router,
    education_router,
    employment_router,
    person_career_router,
)
from app.modules.codes import router as codes_router
from app.modules.documents import (
    document_groups_router,
    documents_router,
    person_documents_router,
    upload_sessions_router,
)
from app.modules.people import router as people_router
from app.modules.projects import person_projects_router, projects_router
from app.modules.users import router as users_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(codes_router)
api_router.include_router(users_router)
api_router.include_router(people_router)
api_router.include_router(person_projects_router)
api_router.include_router(projects_router)
api_router.include_router(person_career_router)
api_router.include_router(employment_router)
api_router.include_router(education_router)
api_router.include_router(certifications_router)
api_router.include_router(upload_sessions_router)
api_router.include_router(person_documents_router)
api_router.include_router(documents_router)
api_router.include_router(document_groups_router)
