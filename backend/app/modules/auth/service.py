"""Auth business flow: login / logout / current user."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import InvalidCredentialsError, UnauthorizedError
from app.core.security import verify_password
from app.db.models.user import AppUser
from app.modules.auth.repository import AuthRepository
from app.modules.auth.session_store import SessionRecord, SessionStore


@dataclass(frozen=True)
class LoginResult:
    user: AppUser
    session_id: str
    csrf_token: str


class AuthService:
    def __init__(
        self,
        db: Session,
        sessions: SessionStore | None = None,
    ) -> None:
        self.db = db
        self.repo = AuthRepository(db)
        self.sessions = sessions or SessionStore()

    def login(self, login_id: str, password: str) -> LoginResult:
        user = self.repo.get_by_login_id(login_id)
        password_hash = user.password_hash if user is not None else None
        password_ok = verify_password(password, password_hash)

        if user is None or not password_ok or user.status != "ACTIVE":
            self.repo.add_audit(
                action_type="AUTH_LOGIN_FAILED",
                user_id=user.id if user is not None else None,
                metadata={"login_id": login_id},
            )
            self.db.commit()
            raise InvalidCredentialsError()

        session_id, record = self.sessions.create_session(user.id)
        self.repo.touch_last_login(user)
        self.repo.add_audit(
            action_type="AUTH_LOGIN_SUCCESS",
            user_id=user.id,
            target_id=user.id,
            metadata={"login_id": user.login_id},
        )
        self.db.commit()
        self.db.refresh(user)
        return LoginResult(user=user, session_id=session_id, csrf_token=record.csrf_token)

    def logout(self, session_id: str, user: AppUser) -> None:
        self.sessions.invalidate_session(session_id)
        self.repo.add_audit(
            action_type="AUTH_LOGOUT",
            user_id=user.id,
            target_id=user.id,
            metadata={"login_id": user.login_id},
        )
        self.db.commit()

    def resolve_user_from_session(self, session_id: str) -> tuple[AppUser, SessionRecord]:
        record = self.sessions.get_session(session_id)
        if record is None:
            raise UnauthorizedError("로그인이 필요합니다.")

        user = self.repo.get_by_id(record.user_id)
        if user is None or user.status != "ACTIVE":
            self.sessions.invalidate_session(session_id)
            raise UnauthorizedError("로그인이 필요합니다.")

        return user, record
