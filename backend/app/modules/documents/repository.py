"""Document / upload-session DB access."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models.code import CodeMaster
from app.db.models.document import Document, DocumentGroup
from app.db.models.person import Person
from app.db.models.revision import AuditLog
from app.db.models.upload import UploadSession, UploadTempFile


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Person / codes ---

    def get_person(self, person_id: UUID, *, for_update: bool = False) -> Person | None:
        stmt = select(Person).where(Person.id == person_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_code(self, code: str) -> CodeMaster | None:
        return self.db.execute(
            select(CodeMaster).where(CodeMaster.code == code)
        ).scalar_one_or_none()

    # --- Upload session ---

    def create_session(
        self, *, created_by: UUID, expires_at: datetime | None
    ) -> UploadSession:
        row = UploadSession(
            status="UPLOADING",
            created_by=created_by,
            expires_at=expires_at,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def get_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> UploadSession | None:
        stmt = select(UploadSession).where(UploadSession.id == session_id)
        if for_update:
            # Always refresh from DB so concurrent cancel/resolve status wins
            # over a stale identity-map instance in long-lived sessions.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_temp_files(self, session_id: UUID) -> list[UploadTempFile]:
        return list(
            self.db.execute(
                select(UploadTempFile)
                .where(UploadTempFile.upload_session_id == session_id)
                .order_by(UploadTempFile.created_at.asc())
            ).scalars().all()
        )

    def count_temp_files(self, session_id: UUID) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(UploadTempFile)
                .where(UploadTempFile.upload_session_id == session_id)
            ).scalar_one()
        )

    def get_temp_file(
        self, file_id: UUID, *, for_update: bool = False
    ) -> UploadTempFile | None:
        stmt = select(UploadTempFile).where(UploadTempFile.id == file_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def create_temp_file(self, **fields: Any) -> UploadTempFile:
        row = UploadTempFile(**fields)
        self.db.add(row)
        self.db.flush()
        return row

    def delete_temp_file(self, file_id: UUID) -> None:
        self.db.execute(delete(UploadTempFile).where(UploadTempFile.id == file_id))
        self.db.flush()

    def find_sha256_in_session(
        self, session_id: UUID, sha256: str
    ) -> UploadTempFile | None:
        return self.db.execute(
            select(UploadTempFile).where(
                UploadTempFile.upload_session_id == session_id,
                UploadTempFile.sha256 == sha256,
            )
        ).scalar_one_or_none()

    def find_document_by_sha256(self, sha256: str) -> Document | None:
        return self.db.execute(
            select(Document)
            .where(Document.sha256 == sha256, Document.deleted_at.is_(None))
            .order_by(Document.uploaded_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    # --- Document group / document ---

    def get_group(
        self, group_id: UUID, *, for_update: bool = False, include_deleted: bool = False
    ) -> DocumentGroup | None:
        stmt = select(DocumentGroup).where(DocumentGroup.id == group_id)
        if not include_deleted:
            stmt = stmt.where(DocumentGroup.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def create_group(
        self, *, person_id: UUID, document_type_code: str, title: str
    ) -> DocumentGroup:
        row = DocumentGroup(
            person_id=person_id,
            document_type_code=document_type_code,
            title=title,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def max_version_no(self, group_id: UUID) -> int:
        value = self.db.execute(
            select(func.max(Document.version_no)).where(
                Document.document_group_id == group_id
            )
        ).scalar_one()
        return int(value or 0)

    def clear_latest(self, group_id: UUID) -> None:
        self.db.execute(
            update(Document)
            .where(
                Document.document_group_id == group_id,
                Document.is_latest.is_(True),
                Document.deleted_at.is_(None),
            )
            .values(is_latest=False, updated_at=datetime.now(UTC))
        )
        self.db.flush()

    def create_document(self, **fields: Any) -> Document:
        row = Document(**fields)
        self.db.add(row)
        self.db.flush()
        return row

    def get_document(
        self,
        document_id: UUID,
        *,
        for_update: bool = False,
        include_deleted: bool = False,
    ) -> Document | None:
        stmt = select(Document).where(Document.id == document_id)
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def list_person_documents(
        self, person_id: UUID, *, include_deleted: bool = False
    ) -> list[tuple[Document, DocumentGroup, str | None]]:
        stmt = (
            select(Document, DocumentGroup, CodeMaster.name)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .outerjoin(
                CodeMaster, CodeMaster.code == DocumentGroup.document_type_code
            )
            .where(DocumentGroup.person_id == person_id)
        )
        if not include_deleted:
            stmt = stmt.where(
                Document.deleted_at.is_(None), DocumentGroup.deleted_at.is_(None)
            )
        stmt = stmt.order_by(
            Document.uploaded_at.desc(), Document.version_no.desc()
        )
        return list(self.db.execute(stmt).all())

    def list_group_versions(
        self, group_id: UUID, *, include_deleted: bool = False
    ) -> list[tuple[Document, DocumentGroup, str | None]]:
        stmt = (
            select(Document, DocumentGroup, CodeMaster.name)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .outerjoin(
                CodeMaster, CodeMaster.code == DocumentGroup.document_type_code
            )
            .where(Document.document_group_id == group_id)
        )
        if not include_deleted:
            stmt = stmt.where(Document.deleted_at.is_(None))
        stmt = stmt.order_by(Document.version_no.desc())
        return list(self.db.execute(stmt).all())

    def soft_delete_document(self, document: Document) -> None:
        now = datetime.now(UTC)
        document.deleted_at = now
        document.updated_at = now
        if document.is_latest:
            document.is_latest = False
        self.db.add(document)
        self.db.flush()

    def promote_latest_among_active(self, group_id: UUID) -> Document | None:
        """Set the highest active version as latest after a delete."""
        self.db.execute(
            update(Document)
            .where(Document.document_group_id == group_id)
            .values(is_latest=False)
        )
        latest = self.db.execute(
            select(Document)
            .where(
                Document.document_group_id == group_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.version_no.desc())
            .limit(1)
            .with_for_update()
        ).scalar_one_or_none()
        if latest is not None:
            latest.is_latest = True
            latest.updated_at = datetime.now(UTC)
            self.db.add(latest)
            self.db.flush()
        return latest

    def restore_document(self, document: Document) -> None:
        document.deleted_at = None
        document.updated_at = datetime.now(UTC)
        self.db.add(document)
        self.db.flush()
        # Restore does not forcibly steal latest; recompute among active versions.
        self.promote_latest_among_active(document.document_group_id)

    def add_audit(
        self,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        target_type: str,
        target_id: UUID | None,
        before: dict | None = None,
        after: dict | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                user_id=actor_user_id,
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                before_json=before,
                after_json=after,
                metadata_json=metadata or {},
            )
        )
