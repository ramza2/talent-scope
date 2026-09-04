"""User administration business logic."""

from __future__ import annotations

import logging
import math
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import (
    CannotDeactivateSelfError,
    InvalidUserRoleError,
    InvalidUserStatusError,
    LastActiveAdminRequiredError,
    NotFoundError,
    UserLoginIdExistsError,
    ValidationAppError,
)
from app.core.security import hash_password
from app.db.models.user import AppUser
from app.modules.auth.session_store import SessionStore
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import (
    ALLOWED_ROLES,
    ALLOWED_STATUSES,
    MIN_PASSWORD_LENGTH,
    PageMeta,
    ResetPasswordRequest,
    UserCreateRequest,
    UserItem,
    UserUpdateRequest,
)

logger = logging.getLogger(__name__)


class UserService:
    def __init__(
        self,
        db: Session,
        sessions: SessionStore | None = None,
    ) -> None:
        self.db = db
        self.repo = UserRepository(db)
        self.sessions = sessions or SessionStore()

    def _to_item(self, user: AppUser) -> UserItem:
        return UserItem(
            id=user.id,
            login_id=user.login_id,
            name=user.name,
            email=user.email,
            department=user.department,
            role=user.role,  # type: ignore[arg-type]
            status=user.status,  # type: ignore[arg-type]
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def _safe_snapshot(self, user: AppUser) -> dict:
        return {
            "login_id": user.login_id,
            "name": user.name,
            "email": user.email,
            "department": user.department,
            "role": user.role,
            "status": user.status,
        }

    def _invalidate_sessions_best_effort(self, user_id: UUID, reason: str) -> None:
        """Invalidate Redis sessions after successful DB commit.

        Policy: DB Role/Status is Source of Truth. Redis failure must not
        roll back DB changes; subsequent requests still re-check DB status.
        """
        try:
            removed = self.sessions.invalidate_user_sessions(user_id)
            logger.info(
                "user_sessions_invalidated user_id=%s reason=%s removed=%s",
                str(user_id),
                reason,
                removed,
            )
        except Exception:
            logger.exception(
                "user_sessions_invalidate_failed user_id=%s reason=%s",
                str(user_id),
                reason,
            )

    def list_users(
        self,
        *,
        q: str | None = None,
        role: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[UserItem], PageMeta]:
        if role is not None and role not in ALLOWED_ROLES:
            raise InvalidUserRoleError()
        if status is not None and status not in ALLOWED_STATUSES:
            raise InvalidUserStatusError()
        if page < 1:
            raise ValidationAppError("page는 1 이상이어야 합니다.")
        if page_size < 1 or page_size > 100:
            raise ValidationAppError("page_size는 1~100 사이여야 합니다.")

        rows, total = self.repo.list_users(
            q=q,
            role=role,
            status=status,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        total_pages = math.ceil(total / page_size) if total else 0
        meta = PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
        return [self._to_item(r) for r in rows], meta

    def get_user(self, user_id: UUID) -> UserItem:
        user = self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없습니다.")
        return self._to_item(user)

    def create_user(
        self,
        payload: UserCreateRequest,
        actor_user_id: UUID,
    ) -> UserItem:
        if payload.role not in ALLOWED_ROLES:
            raise InvalidUserRoleError()
        if self.repo.get_by_login_id(payload.login_id) is not None:
            raise UserLoginIdExistsError()
        if len(payload.password) < MIN_PASSWORD_LENGTH:
            raise ValidationAppError(
                f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
            )

        user = self.repo.create_user(
            login_id=payload.login_id,
            password_hash=hash_password(payload.password),
            name=payload.name,
            role=payload.role,
            email=payload.email,
            department=payload.department,
        )
        self.repo.add_audit(
            action_type="USER_CREATE",
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            after=self._safe_snapshot(user),
        )
        self.db.commit()
        self.db.refresh(user)
        return self._to_item(user)

    def update_user(
        self,
        user_id: UUID,
        payload: UserUpdateRequest,
        actor_user_id: UUID,
    ) -> UserItem:
        user = self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없습니다.")

        fields_set = payload.model_fields_set
        new_role = payload.role if "role" in fields_set else None
        new_status = payload.status if "status" in fields_set else None

        if new_role is not None and new_role not in ALLOWED_ROLES:
            raise InvalidUserRoleError()
        if new_status is not None and new_status not in ALLOWED_STATUSES:
            raise InvalidUserStatusError()

        if new_status == "INACTIVE" and user.id == actor_user_id:
            raise CannotDeactivateSelfError()

        becoming_non_admin = (
            user.role == "ADMIN"
            and user.status == "ACTIVE"
            and (
                (new_role is not None and new_role != "ADMIN")
                or (new_status is not None and new_status != "ACTIVE")
            )
        )
        if becoming_non_admin and self.repo.count_active_admins() <= 1:
            raise LastActiveAdminRequiredError()

        before = self._safe_snapshot(user)
        role_changed = False
        status_changed = False

        if "name" in fields_set and payload.name is not None:
            user.name = payload.name
        if "email" in fields_set:
            user.email = payload.email
        if "department" in fields_set:
            user.department = payload.department
        if new_role is not None and new_role != user.role:
            user.role = new_role
            role_changed = True
        if new_status is not None and new_status != user.status:
            user.status = new_status
            status_changed = True

        self.db.add(user)
        self.repo.add_audit(
            action_type="USER_UPDATE",
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            before=before,
            after=self._safe_snapshot(user),
        )
        self.db.commit()
        self.db.refresh(user)

        if role_changed or status_changed:
            reason = "role_change" if role_changed else "status_change"
            if role_changed and status_changed:
                reason = "role_and_status_change"
            self._invalidate_sessions_best_effort(user.id, reason)

        return self._to_item(user)

    def reset_password(
        self,
        user_id: UUID,
        payload: ResetPasswordRequest,
        actor_user_id: UUID,
    ) -> None:
        user = self.repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("사용자를 찾을 수 없습니다.")
        if len(payload.new_password) < MIN_PASSWORD_LENGTH:
            raise ValidationAppError(
                f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
            )

        user.password_hash = hash_password(payload.new_password)
        self.db.add(user)
        self.repo.add_audit(
            action_type="USER_RESET_PASSWORD",
            actor_user_id=actor_user_id,
            target_user_id=user.id,
            metadata={"login_id": user.login_id},
        )
        self.db.commit()
        self._invalidate_sessions_best_effort(user.id, "password_reset")
