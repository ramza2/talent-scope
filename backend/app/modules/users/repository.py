"""User administration DB access."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models.revision import AuditLog
from app.db.models.user import AppUser


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: UUID) -> AppUser | None:
        return self.db.execute(
            select(AppUser).where(AppUser.id == user_id)
        ).scalar_one_or_none()

    def get_by_login_id(self, login_id: str) -> AppUser | None:
        return self.db.execute(
            select(AppUser).where(AppUser.login_id == login_id)
        ).scalar_one_or_none()

    def list_users(
        self,
        *,
        q: str | None = None,
        role: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AppUser], int]:
        filters = []
        if role:
            filters.append(AppUser.role == role)
        if status:
            filters.append(AppUser.status == status)
        if q:
            pattern = f"%{q.strip()}%"
            filters.append(
                or_(
                    AppUser.login_id.ilike(pattern),
                    AppUser.name.ilike(pattern),
                    AppUser.email.ilike(pattern),
                    AppUser.department.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(AppUser)
        list_stmt = select(AppUser)
        for f in filters:
            count_stmt = count_stmt.where(f)
            list_stmt = list_stmt.where(f)

        total = int(self.db.execute(count_stmt).scalar_one())
        list_stmt = (
            list_stmt.order_by(AppUser.created_at.desc(), AppUser.login_id.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(self.db.execute(list_stmt).scalars().all())
        return rows, total

    def count_active_admins(self) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(AppUser)
                .where(AppUser.role == "ADMIN", AppUser.status == "ACTIVE")
            ).scalar_one()
        )

    def create_user(
        self,
        *,
        login_id: str,
        password_hash: str,
        name: str,
        role: str,
        email: str | None = None,
        department: str | None = None,
        status: str = "ACTIVE",
    ) -> AppUser:
        user = AppUser(
            login_id=login_id,
            password_hash=password_hash,
            name=name,
            role=role,
            status=status,
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
        actor_user_id: UUID | None,
        target_user_id: UUID,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry = AuditLog(
            user_id=actor_user_id,
            action_type=action_type,
            target_type="USER",
            target_id=target_user_id,
            before_json=before,
            after_json=after,
            metadata_json=metadata or {},
        )
        self.db.add(entry)
