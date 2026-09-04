"""Redis client helpers for session storage."""

from __future__ import annotations

from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.exceptions import SessionStoreUnavailableError


@lru_cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)


def redis_call(operation_name: str, fn):
    """Execute a Redis operation and map connectivity failures to 503."""
    try:
        return fn()
    except RedisError as exc:
        raise SessionStoreUnavailableError(
            f"Session store unavailable during {operation_name}"
        ) from exc
