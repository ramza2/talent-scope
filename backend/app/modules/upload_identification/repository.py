"""Persistence helpers for upload identification."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.upload import UploadSession, UploadTempFile
from app.modules.documents.repository import DocumentRepository


class UploadIdentificationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)

    def get_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> UploadSession | None:
        return self.documents.get_session(session_id, for_update=for_update)

    def list_temp_files(self, session_id: UUID) -> list[UploadTempFile]:
        return self.documents.list_temp_files(session_id)

    def save_identified(
        self,
        session: UploadSession,
        *,
        name: str | None,
        company: str | None,
        phone: str | None,
        email: str | None,
        duplicates: list[dict[str, Any]],
    ) -> None:
        session.status = "IDENTIFIED"
        session.identified_name = name
        session.identified_company = company
        session.identified_phone = phone
        session.identified_email = email
        session.duplicate_result_json = duplicates
        self.db.add(session)
        self.db.flush()

    def restore_status(self, session: UploadSession, status: str) -> None:
        session.status = status
        self.db.add(session)
        self.db.flush()

    def add_audit(
        self,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        session_id: UUID,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.documents.add_audit(
            action_type=action_type,
            actor_user_id=actor_user_id,
            target_type="UPLOAD_SESSION",
            target_id=session_id,
            after=after,
            metadata=metadata,
        )
