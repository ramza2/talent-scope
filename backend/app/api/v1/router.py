"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import health
from app.modules.auth import router as auth_router
from app.modules.codes import router as codes_router
from app.modules.people import router as people_router
from app.modules.users import router as users_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(codes_router)
api_router.include_router(users_router)
api_router.include_router(people_router)
