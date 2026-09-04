"""Auth-related DB access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.revision import AuditLog
from app.db.models.user import AppUser


class AuthRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_login_id(self, login_id: str) -> AppUser | None:
        stmt = select(AppUser).where(AppUser.login_id == login_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_id(self, user_id: UUID) -> AppUser | None:
        stmt = select(AppUser).where(AppUser.id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def touch_last_login(self, user: AppUser) -> None:
        user.last_login_at = datetime.now(UTC)
        self.db.add(user)

    def create_user(
        self,
        *,
        login_id: str,
        password_hash: str,
        name: str,
        role: str,
        email: str | None = None,
        department: str | None = None,
    ) -> AppUser:
        user = AppUser(
            login_id=login_id,
            password_hash=password_hash,
            name=name,
            role=role,
            status="ACTIVE",
            email=email,
            department=department,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def add_audit(
        self,
        *,
        action_type: str,
        user_id: UUID | None,
        target_type: str = "AUTH",
        target_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Never include password / session id / csrf in metadata.
        entry = AuditLog(
            user_id=user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            metadata_json=metadata or {},
        )
        self.db.add(entry)
