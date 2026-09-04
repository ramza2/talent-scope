"""Health check endpoints.

GET /api/v1/health/live  — process liveness
GET /api/v1/health/ready — dependency readiness (Postgres required; Redis best-effort)

LLM/VLM status is intentionally excluded from readiness.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from redis import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter(prefix="/health")


class LiveResponse(BaseModel):
    status: str = "ok"


class ReadyResponse(BaseModel):
    status: str
    database: str
    redis: str = Field(description="ok | error | skipped")


def _check_database() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"


def _check_redis() -> str:
    settings = get_settings()
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1.0)
        try:
            if client.ping():
                return "ok"
            return "error"
        finally:
            client.close()
    except Exception:
        return "error"


@router.get("/live", response_model=LiveResponse)
def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
def ready(response: Response) -> ReadyResponse:
    database = _check_database()
    redis_status = _check_redis()

    # Database is required for readiness. Redis is reported but does not fail
    # the endpoint alone in Cloud Agent environments where Redis may be absent.
    # If both DB and Redis are ok → ready; if DB fails → not_ready.
    if database == "ok":
        payload = ReadyResponse(status="ready", database=database, redis=redis_status)
        response.status_code = status.HTTP_200_OK
        return payload

    payload = ReadyResponse(status="not_ready", database=database, redis=redis_status)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload
