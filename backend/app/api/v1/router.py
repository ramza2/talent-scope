"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import health
from app.modules.auth import router as auth_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth_router)
