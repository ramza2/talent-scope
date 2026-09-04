"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.logging import configure_logging
from app.core.problem import register_exception_handlers

# Import models so Alembic / metadata consumers see all tables.
import app.db.models  # noqa: F401


def create_app() -> FastAPI:
    configure_logging()

    application = FastAPI(
        title="TalentScope API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Same-Origin only: Browser → Frontend → /api/* (Vite proxy / Traefik).
    # Do not enable CORSMiddleware for cross-origin cookie auth.

    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
