"""Redis server-session store.

Cookie holds opaque session id; Redis keys use SHA-256(session_id).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.redis import get_redis, redis_call
from app.core.security import generate_csrf_token, generate_session_id, hash_session_id


@dataclass(frozen=True)
class SessionRecord:
    user_id: UUID
    csrf_token: str
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "user_id": str(self.user_id),
            "csrf_token": self.csrf_token,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionRecord:
        return cls(
            user_id=UUID(str(raw["user_id"])),
            csrf_token=str(raw["csrf_token"]),
            created_at=str(raw["created_at"]),
        )


class SessionStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._redis = get_redis()

    def _session_key(self, session_hash: str) -> str:
        return f"{self.settings.redis_key_prefix}:session:{session_hash}"

    def _user_sessions_key(self, user_id: UUID) -> str:
        return f"{self.settings.redis_key_prefix}:user_sessions:{user_id}"

    def create_session(self, user_id: UUID) -> tuple[str, SessionRecord]:
        session_id = generate_session_id()
        session_hash = hash_session_id(session_id)
        record = SessionRecord(
            user_id=user_id,
            csrf_token=generate_csrf_token(),
            created_at=datetime.now(UTC).isoformat(),
        )
        ttl = self.settings.session_ttl_seconds
        session_key = self._session_key(session_hash)
        user_key = self._user_sessions_key(user_id)
        payload = json.dumps(record.to_dict())

        def _op() -> None:
            pipe = self._redis.pipeline()
            pipe.set(session_key, payload, ex=ttl)
            pipe.sadd(user_key, session_hash)
            pipe.expire(user_key, ttl)
            pipe.execute()

        redis_call("create_session", _op)
        return session_id, record

    def get_session(self, session_id: str) -> SessionRecord | None:
        session_hash = hash_session_id(session_id)
        key = self._session_key(session_hash)

        def _op() -> str | None:
            return self._redis.get(key)

        raw = redis_call("get_session", _op)
        if not raw:
            return None
        data = json.loads(raw)
        return SessionRecord.from_dict(data)

    def invalidate_session(self, session_id: str) -> None:
        session_hash = hash_session_id(session_id)
        key = self._session_key(session_hash)

        def _load() -> str | None:
            return self._redis.get(key)

        raw = redis_call("invalidate_session_load", _load)
        if not raw:
            return
        data = json.loads(raw)
        user_id = UUID(str(data["user_id"]))
        user_key = self._user_sessions_key(user_id)

        def _delete() -> None:
            pipe = self._redis.pipeline()
            pipe.delete(key)
            pipe.srem(user_key, session_hash)
            pipe.execute()

        redis_call("invalidate_session_delete", _delete)

    def invalidate_user_sessions(self, user_id: UUID) -> int:
        """Invalidate all sessions for a user. Returns removed session count."""
        user_key = self._user_sessions_key(user_id)

        def _members() -> set[str]:
            return set(self._redis.smembers(user_key) or set())

        hashes = redis_call("list_user_sessions", _members)
        if not hashes:
            return 0

        def _delete() -> int:
            pipe = self._redis.pipeline()
            for session_hash in hashes:
                pipe.delete(self._session_key(session_hash))
            pipe.delete(user_key)
            pipe.execute()
            return len(hashes)

        return int(redis_call("invalidate_user_sessions", _delete))
