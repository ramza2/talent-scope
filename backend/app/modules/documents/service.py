"""Document / upload-session business logic."""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AIQueueUnavailableError,
    NotFoundError,
    UploadSessionStateConflictError,
    PayloadTooLargeError,
    PreviewUnavailableError,
    UnsupportedMediaTypeError,
    ValidationAppError,
)
from app.db.models.document import Document, DocumentGroup
from app.db.models.upload import UploadSession, UploadTempFile
from app.modules.documents.constants import (
    ALLOWED_EXTENSIONS,
    BLOCKED_EXTENSIONS,
    CONVERSION_PENDING_EXTENSIONS,
    INLINE_PREVIEW_EXTENSIONS,
    canonical_mime,
    validate_inline_signature,
)
from app.modules.documents.repository import DocumentRepository
from app.modules.documents.schemas import (
    DocumentDetail,
    DocumentListItem,
    DocumentResolutionItem,
    ResolveRequest,
    TempFileItem,
    TempFilePatchRequest,
    UploadSessionCreateRequest,
    UploadSessionDetail,
)
from app.modules.people.repository import PeopleRepository
from app.modules.people.snapshot import build_confirmed_profile_snapshot
from app.modules.people.visibility import ensure_person_readable
from app.storage.base import ObjectStorage, StoredObject
from app.storage.s3 import get_object_storage

logger = logging.getLogger(__name__)

_SAFE_FILENAME = re.compile(r"[^\w.\-()+ ]+", re.UNICODE)


def _normalize_filename(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _SAFE_FILENAME.sub("_", base).strip(". ")
    return (cleaned or "upload.bin")[:500]


def _extension_of(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    if len(parts) != 2:
        return ""
    return parts[1].lower().strip()


def content_disposition_attachment(filename: str) -> str:
    ascii_fallback = _SAFE_FILENAME.sub("_", filename).encode("ascii", "ignore").decode() or "download"
    encoded = quote(filename)
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def content_disposition_inline(filename: str) -> str:
    ascii_fallback = _SAFE_FILENAME.sub("_", filename).encode("ascii", "ignore").decode() or "preview"
    encoded = quote(filename)
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


class DocumentService:
    def __init__(
        self,
        db: Session,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.settings = settings or get_settings()
        self.storage = storage or get_object_storage()

    # --- helpers ---

    def _max_bytes(self) -> int:
        return int(self.settings.upload_max_file_size_mb) * 1024 * 1024

    def _temp_key(self, session_id: UUID, file_id: UUID) -> str:
        return f"temp/{session_id}/{file_id}"

    def _document_key(
        self, person_id: UUID, group_id: UUID, document_id: UUID
    ) -> str:
        return f"documents/{person_id}/{group_id}/{document_id}/original"

    def _require_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> UploadSession:
        session = self.repo.get_session(session_id, for_update=for_update)
        if session is None:
            raise NotFoundError("업로드 세션을 찾을 수 없습니다.")
        return session

    def _ensure_mutable_session(self, session: UploadSession) -> None:
        if session.status in {"RESOLVED", "CANCELLED", "EXPIRED"}:
            raise ValidationAppError(
                f"상태가 {session.status}인 세션은 변경할 수 없습니다."
            )

    def _ensure_file_mutable_session(self, session: UploadSession) -> None:
        """File mutations blocked while IDENTIFYING or terminal."""
        if session.status == "IDENTIFYING":
            raise UploadSessionStateConflictError(
                "인력 식별이 진행 중에는 파일을 변경할 수 없습니다."
            )
        self._ensure_mutable_session(session)

    def _invalidate_identity_if_needed(self, session: UploadSession) -> None:
        """Clear stale identify results after file mutations on IDENTIFIED."""
        if session.status != "IDENTIFIED":
            return
        session.status = "UPLOADING"
        session.identified_name = None
        session.identified_company = None
        session.identified_phone = None
        session.identified_email = None
        session.duplicate_result_json = []
        self.db.add(session)

    def _validate_doc_type(self, code: str, *, require_active: bool = True) -> None:
        row = self.repo.get_code(code)
        if row is None or row.code_type != "DOC_TYPE":
            raise ValidationAppError(f"문서종류 코드가 올바르지 않습니다: {code}")
        if require_active and not row.is_active:
            raise ValidationAppError(f"비활성 문서종류 코드입니다: {code}")

    def _suggest_doc_type(self, filename: str) -> str | None:
        lower = filename.lower()
        mapping = [
            (("이력서", "resume", "cv"), "DOC-RESUME"),
            (("경력기술", "career"), "DOC-CAREER"),
            (("프로필", "profile"), "DOC-PROFILE"),
            (("자격", "cert"), "DOC-CERT"),
            (("kosa", "경력증명"), "DOC-KOSA"),
            (("포트폴리오", "portfolio"), "DOC-PORTFOLIO"),
            (("학력", "diploma", "졸업"), "DOC-EDU"),
        ]
        for keys, code in mapping:
            if any(k in lower for k in keys):
                if self.repo.get_code(code) is not None:
                    return code
        other = "DOC-OTHER"
        if self.repo.get_code(other) is not None:
            return other
        return None

    def _temp_item(self, row: UploadTempFile, *, suggested: bool = False) -> TempFileItem:
        dup = None
        if row.validation_status == "DUPLICATE":
            dup = {"reason": "sha256", "message": row.validation_message}
        return TempFileItem(
            temp_file_id=row.id,
            original_filename=row.original_filename,
            file_size=row.file_size,
            mime_type=row.mime_type,
            extension=row.extension,
            document_type_code=row.document_type_code,
            document_type_suggested=suggested,
            validation_status=row.validation_status,
            validation_message=row.validation_message,
            sha256=row.sha256,
            duplicate=dup,
            created_at=row.created_at,
        )

    def _session_detail(
        self, session: UploadSession, *, target_person_id: UUID | None = None
    ) -> UploadSessionDetail:
        files = [self._temp_item(f) for f in self.repo.list_temp_files(session.id)]
        identity = None
        if any(
            [
                session.identified_name,
                session.identified_company,
                session.identified_phone,
                session.identified_email,
            ]
        ):
            identity = {
                "name": session.identified_name,
                "company": session.identified_company,
                "phone": session.identified_phone,
                "email": session.identified_email,
            }
        return UploadSessionDetail(
            id=session.id,
            status=session.status,
            resolved_person_id=session.resolved_person_id,
            target_person_id=target_person_id,
            created_by=session.created_by,
            created_at=session.created_at,
            expires_at=session.expires_at,
            files=files,
            identity=identity,
            duplicate_candidates=list(session.duplicate_result_json or []),
        )

    # --- Upload session ---

    def create_session(
        self, payload: UploadSessionCreateRequest, actor_user_id: UUID
    ) -> UploadSessionDetail:
        target = payload.target_person_id
        if target is not None:
            person = self.repo.get_person(target)
            if person is None:
                raise NotFoundError("대상 인력을 찾을 수 없습니다.")
            # Not persisted — schema has no target_person_id column.
        expires = datetime.now(UTC) + timedelta(
            hours=self.settings.upload_session_ttl_hours
        )
        session = self.repo.create_session(created_by=actor_user_id, expires_at=expires)
        self.repo.add_audit(
            action_type="UPLOAD_SESSION_CREATE",
            actor_user_id=actor_user_id,
            target_type="UPLOAD_SESSION",
            target_id=session.id,
            after={"status": session.status},
            metadata={"target_person_id": str(target) if target else None},
        )
        self.db.commit()
        self.db.refresh(session)
        return self._session_detail(session, target_person_id=target)

    def get_session(self, session_id: UUID) -> UploadSessionDetail:
        session = self._require_session(session_id)
        return self._session_detail(session)

    def upload_files(
        self,
        session_id: UUID,
        files: list[UploadFile],
        actor_user_id: UUID,
    ) -> list[TempFileItem]:
        session = self._require_session(session_id, for_update=True)
        self._ensure_file_mutable_session(session)
        if not files:
            raise ValidationAppError("업로드할 파일이 없습니다.")

        current = self.repo.count_temp_files(session_id)
        if current + len(files) > self.settings.upload_max_files_per_session:
            raise PayloadTooLargeError(
                f"세션당 최대 {self.settings.upload_max_files_per_session}개까지 업로드할 수 있습니다."
            )

        max_bytes = self._max_bytes()
        results: list[TempFileItem] = []
        # Track only objects created by THIS request for compensation.
        created_keys: list[str] = []
        try:
            for upload in files:
                item = self._ingest_one_file(
                    session,
                    upload,
                    max_bytes=max_bytes,
                    created_keys=created_keys,
                )
                results.append(item)

            self._invalidate_identity_if_needed(session)
            self.repo.add_audit(
                action_type="UPLOAD_TEMP_FILE_CREATE",
                actor_user_id=actor_user_id,
                target_type="UPLOAD_SESSION",
                target_id=session.id,
                after={"file_ids": [str(i.temp_file_id) for i in results]},
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            for key in created_keys:
                try:
                    self.storage.delete(key)
                except Exception:
                    logger.warning(
                        "upload rollback: orphan temp cleanup failed key=%s",
                        key,
                        exc_info=True,
                    )
            raise
        return results

    def _ingest_one_file(
        self,
        session: UploadSession,
        upload: UploadFile,
        *,
        max_bytes: int,
        created_keys: list[str],
    ) -> TempFileItem:
        original = _normalize_filename(upload.filename or "upload.bin")
        ext = _extension_of(original)
        if not ext or ext in BLOCKED_EXTENSIONS or ext not in ALLOWED_EXTENSIONS:
            raise UnsupportedMediaTypeError(f"지원하지 않는 확장자입니다: .{ext or '?'}")

        file_id = uuid4()
        key = self._temp_key(session.id, file_id)
        hasher = hashlib.sha256()
        size = 0
        # Stream to a spooled temp file — avoid holding the whole upload in RAM.
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLargeError(
                        f"파일 최대 크기({self.settings.upload_max_file_size_mb}MB)를 초과했습니다."
                    )
                hasher.update(chunk)
                spool.write(chunk)
            if size == 0:
                raise ValidationAppError("빈 파일은 업로드할 수 없습니다.")

            spool.seek(0)
            header = spool.read(16)
            spool.seek(0)
            if not validate_inline_signature(ext, header):
                raise UnsupportedMediaTypeError(
                    f"파일 내용이 .{ext} 형식과 일치하지 않습니다."
                )

            # Never trust client-supplied Content-Type; use extension canonical MIME.
            mime = canonical_mime(ext)
            sha256 = hasher.hexdigest()

            suggested_code = self._suggest_doc_type(original)
            suggested = suggested_code is not None
            validation_status = "VALID"
            validation_message = None
            if ext in CONVERSION_PENDING_EXTENSIONS:
                validation_message = (
                    "미리보기 변환(LibreOffice)이 아직 구현되지 않았습니다."
                )

            dup_temp = self.repo.find_sha256_in_session(session.id, sha256)
            dup_doc = self.repo.find_document_by_sha256(sha256)
            if dup_temp is not None or dup_doc is not None:
                validation_status = "DUPLICATE"
                validation_message = "동일 SHA-256 파일이 이미 존재합니다."

            self.storage.put_fileobj(key, spool, length=size, content_type=mime)
            # After put succeeds, this request owns the key for rollback cleanup —
            # including the case where DB create_temp_file fails next.
            created_keys.append(key)

            row = self.repo.create_temp_file(
                id=file_id,
                upload_session_id=session.id,
                document_type_code=suggested_code,
                original_filename=original,
                temp_storage_key=key,
                mime_type=mime,
                extension=ext,
                file_size=size,
                sha256=sha256,
                validation_status=validation_status,
                validation_message=validation_message,
            )
            return self._temp_item(row, suggested=suggested)

    def patch_temp_file(
        self,
        session_id: UUID,
        file_id: UUID,
        payload: TempFilePatchRequest,
        actor_user_id: UUID,
    ) -> TempFileItem:
        session = self._require_session(session_id, for_update=True)
        self._ensure_file_mutable_session(session)
        row = self.repo.get_temp_file(file_id, for_update=True)
        if row is None or row.upload_session_id != session_id:
            raise NotFoundError("임시 파일을 찾을 수 없습니다.")
        code = payload.document_type_code.strip()
        self._validate_doc_type(code)
        before = {"document_type_code": row.document_type_code}
        row.document_type_code = code
        self.db.add(row)
        self._invalidate_identity_if_needed(session)
        self.repo.add_audit(
            action_type="UPLOAD_TEMP_FILE_UPDATE",
            actor_user_id=actor_user_id,
            target_type="UPLOAD_TEMP_FILE",
            target_id=row.id,
            before=before,
            after={"document_type_code": code},
        )
        self.db.commit()
        self.db.refresh(row)
        return self._temp_item(row)

    def delete_temp_file(
        self, session_id: UUID, file_id: UUID, actor_user_id: UUID
    ) -> None:
        """DB-authoritative delete, then best-effort storage cleanup.

        MVP policy: prefer orphan temp objects over dangling DB references.
        After COMMIT succeeds we never resurrect the DB row if storage delete fails.
        """
        session = self._require_session(session_id, for_update=True)
        self._ensure_file_mutable_session(session)
        row = self.repo.get_temp_file(file_id, for_update=True)
        if row is None or row.upload_session_id != session_id:
            raise NotFoundError("임시 파일을 찾을 수 없습니다.")
        key = row.temp_storage_key
        before = {
            "original_filename": row.original_filename,
            "temp_storage_key": key,
        }
        self.repo.delete_temp_file(file_id)
        self._invalidate_identity_if_needed(session)
        self.repo.add_audit(
            action_type="UPLOAD_TEMP_FILE_DELETE",
            actor_user_id=actor_user_id,
            target_type="UPLOAD_TEMP_FILE",
            target_id=file_id,
            before=before,
        )
        self.db.commit()
        try:
            self.storage.delete(key)
        except Exception:
            logger.warning(
                "temp delete: orphan object after DB commit key=%s",
                key,
                exc_info=True,
            )

    def cancel_session(self, session_id: UUID, actor_user_id: UUID) -> None:
        """Cancel session in DB first, then best-effort temp object cleanup.

        Same orphan-over-dangling policy as ``delete_temp_file``.
        """
        session = self._require_session(session_id, for_update=True)
        if session.status == "CANCELLED":
            return
        if session.status == "RESOLVED":
            raise ValidationAppError("이미 확정된 세션은 취소할 수 없습니다.")
        files = self.repo.list_temp_files(session_id)
        temp_keys = [f.temp_storage_key for f in files]
        for f in files:
            self.repo.delete_temp_file(f.id)
        session.status = "CANCELLED"
        self.db.add(session)
        self.repo.add_audit(
            action_type="UPLOAD_SESSION_CANCEL",
            actor_user_id=actor_user_id,
            target_type="UPLOAD_SESSION",
            target_id=session.id,
            after={"status": "CANCELLED"},
        )
        self.db.commit()
        for key in temp_keys:
            try:
                self.storage.delete(key)
            except Exception:
                logger.warning(
                    "cancel: orphan temp after DB commit key=%s",
                    key,
                    exc_info=True,
                )

    def identify(self, session_id: UUID) -> dict:
        """Start async identity extraction. Returns 202 payload fields."""
        session = self._require_session(session_id, for_update=True)
        if session.status in {"RESOLVED", "CANCELLED", "EXPIRED"}:
            raise UploadSessionStateConflictError(
                f"상태가 {session.status}인 세션은 식별할 수 없습니다."
            )
        if session.status == "IDENTIFYING":
            # Idempotent: do not enqueue a second task.
            return {"upload_session_id": session.id, "status": "IDENTIFYING"}

        if session.status not in {"UPLOADING", "IDENTIFIED"}:
            raise UploadSessionStateConflictError(
                f"상태가 {session.status}인 세션은 식별할 수 없습니다."
            )

        files = self.repo.list_temp_files(session_id)
        if not files:
            raise ValidationAppError("식별할 업로드 파일이 없습니다.")

        previous_status = session.status
        session.status = "IDENTIFYING"
        self.db.add(session)
        self.repo.add_audit(
            action_type="UPLOAD_SESSION_IDENTIFY_START",
            actor_user_id=session.created_by,
            target_type="UPLOAD_SESSION",
            target_id=session.id,
            after={"status": "IDENTIFYING", "previous_status": previous_status},
        )
        self.db.commit()

        from app.tasks.analysis_tasks import enqueue_upload_identify

        try:
            enqueue_upload_identify(session_id)
        except Exception:
            logger.exception("identify enqueue failed session_id=%s", session_id)
            # Restore prior status so the session is not stuck IDENTIFYING.
            session = self._require_session(session_id, for_update=True)
            if session.status == "IDENTIFYING":
                session.status = previous_status
                self.db.add(session)
                self.db.commit()
            raise AIQueueUnavailableError()

        return {"upload_session_id": session_id, "status": "IDENTIFYING"}

    # --- Resolve / promote ---

    def resolve(
        self, session_id: UUID, payload: ResolveRequest, actor_user_id: UUID
    ) -> dict:
        if payload.mode == "CREATE_NEW":
            return self._resolve_create_new(session_id, payload, actor_user_id)
        return self._resolve_link_existing(session_id, payload, actor_user_id)

    def _resolve_link_existing(
        self, session_id: UUID, payload: ResolveRequest, actor_user_id: UUID
    ) -> dict:
        if payload.person_id is None:
            raise ValidationAppError("LINK_EXISTING에는 person_id가 필요합니다.")

        session = self._require_session(session_id, for_update=True)
        if session.status == "IDENTIFYING":
            raise UploadSessionStateConflictError(
                "인력 식별이 진행 중에는 확정할 수 없습니다."
            )
        if session.status not in {"UPLOADING", "IDENTIFIED"}:
            raise ValidationAppError(
                f"상태가 {session.status}인 세션은 확정할 수 없습니다."
            )

        person = self.repo.get_person(payload.person_id, for_update=True)
        if person is None or person.deleted_at is not None or person.status == "DELETED":
            raise NotFoundError("인력을 찾을 수 없습니다.")

        people_repo = PeopleRepository(self.db)
        profile = people_repo.get_profile(person.id, for_update=True)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")
        profile_version = int(profile.profile_version)

        document_ids: list[UUID] = []
        created_permanent_keys: list[str] = []
        source_temp_keys: list[str] = []
        try:
            for item in payload.document_resolution:
                doc_id, dest_key, temp_key = self._promote_temp_file(
                    session=session,
                    person_id=person.id,
                    item=item,
                    actor_user_id=actor_user_id,
                    created_permanent_keys=created_permanent_keys,
                )
                document_ids.append(doc_id)
                source_temp_keys.append(temp_key)

            session.status = "RESOLVED"
            session.resolved_person_id = person.id
            self.db.add(session)
            self.repo.add_audit(
                action_type="UPLOAD_SESSION_RESOLVE",
                actor_user_id=actor_user_id,
                target_type="UPLOAD_SESSION",
                target_id=session.id,
                after={
                    "mode": "LINK_EXISTING",
                    "person_id": str(person.id),
                    "document_ids": [str(i) for i in document_ids],
                },
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            for key in created_permanent_keys:
                try:
                    self.storage.delete(key)
                except Exception:
                    logger.exception("compensate delete failed key=%s", key)
            raise

        for key in source_temp_keys:
            try:
                self.storage.delete(key)
            except Exception:
                logger.warning(
                    "resolve: orphan temp after successful commit key=%s",
                    key,
                    exc_info=True,
                )

        self._enqueue_processing(document_ids)

        return {
            "person_id": person.id,
            "document_ids": document_ids,
            "profile_version": profile_version,
            "upload_session_id": session.id,
        }

    def _resolve_create_new(
        self, session_id: UUID, payload: ResolveRequest, actor_user_id: UUID
    ) -> dict:
        if payload.identity is None:
            raise ValidationAppError("CREATE_NEW에는 identity가 필요합니다.")
        identity = payload.identity
        name = identity.name.strip()
        if not name:
            raise ValidationAppError("이름은 필수입니다.")

        for item in payload.document_resolution:
            if item.mode != "NEW_GROUP":
                raise ValidationAppError(
                    "CREATE_NEW에서는 NEW_GROUP만 허용됩니다."
                )

        session = self._require_session(session_id, for_update=True)
        if session.status != "IDENTIFIED":
            raise UploadSessionStateConflictError(
                "CREATE_NEW는 IDENTIFIED 상태에서만 가능합니다."
            )

        people_repo = PeopleRepository(self.db)
        document_ids: list[UUID] = []
        created_permanent_keys: list[str] = []
        source_temp_keys: list[str] = []
        person_id: UUID | None = None
        try:
            person = people_repo.create_person(created_by=actor_user_id)
            person_id = person.id
            profile = people_repo.create_profile(
                person.id,
                name=name[:150],
                phone=(identity.phone.strip()[:50] if identity.phone else None),
                email=(identity.email.strip()[:255] if identity.email else None),
                affiliation_company=(
                    identity.company.strip()[:300] if identity.company else None
                ),
            )
            snapshot = build_confirmed_profile_snapshot(self.db, person.id)
            people_repo.add_revision(
                person_id=person.id,
                revision_no=profile.profile_version,
                snapshot=snapshot,
                created_by=actor_user_id,
                source_type="USER",
            )
            people_repo.add_audit(
                action_type="PERSON_CREATE",
                actor_user_id=actor_user_id,
                person_id=person.id,
                after={
                    "name": profile.name,
                    "affiliation_company": profile.affiliation_company,
                    "phone": profile.phone,
                    "email": profile.email,
                },
                metadata={
                    "upload_session_id": str(session.id),
                    "source": "UPLOAD_IDENTIFY",
                },
            )
            people_repo.enqueue_rebuild_person(person.id, profile.profile_version)

            for item in payload.document_resolution:
                doc_id, dest_key, temp_key = self._promote_temp_file(
                    session=session,
                    person_id=person.id,
                    item=item,
                    actor_user_id=actor_user_id,
                    created_permanent_keys=created_permanent_keys,
                )
                document_ids.append(doc_id)
                source_temp_keys.append(temp_key)

            session.status = "RESOLVED"
            session.resolved_person_id = person.id
            self.db.add(session)
            self.repo.add_audit(
                action_type="UPLOAD_SESSION_RESOLVE",
                actor_user_id=actor_user_id,
                target_type="UPLOAD_SESSION",
                target_id=session.id,
                after={
                    "mode": "CREATE_NEW",
                    "person_id": str(person.id),
                    "document_ids": [str(i) for i in document_ids],
                },
                metadata={"source": "UPLOAD_IDENTIFY"},
            )
            self.db.commit()
            profile_version = int(profile.profile_version)
        except Exception:
            self.db.rollback()
            for key in created_permanent_keys:
                try:
                    self.storage.delete(key)
                except Exception:
                    logger.exception("compensate delete failed key=%s", key)
            raise

        for key in source_temp_keys:
            try:
                self.storage.delete(key)
            except Exception:
                logger.warning(
                    "resolve: orphan temp after successful commit key=%s",
                    key,
                    exc_info=True,
                )

        self._enqueue_processing(document_ids)

        assert person_id is not None
        return {
            "person_id": person_id,
            "document_ids": document_ids,
            "profile_version": profile_version,
            "upload_session_id": session_id,
        }

    def _enqueue_processing(self, document_ids: list[UUID]) -> None:
        from app.tasks.document_tasks import enqueue_document_processing

        for doc_id in document_ids:
            enqueue_document_processing(doc_id)

    def _promote_temp_file(
        self,
        *,
        session: UploadSession,
        person_id: UUID,
        item: DocumentResolutionItem,
        actor_user_id: UUID,
        created_permanent_keys: list[str],
    ) -> tuple[UUID, str, str]:
        """Copy temp→permanent and apply DB changes. Does NOT delete temp object.

        Temp storage cleanup happens only after the outer resolve() COMMIT.

        ``created_permanent_keys`` is the outer resolve compensation list: after
        ``storage.copy`` succeeds the destination key is appended immediately so
        a later DB failure still deletes the permanent object on rollback.
        """
        temp = self.repo.get_temp_file(item.temp_file_id, for_update=True)
        if temp is None or temp.upload_session_id != session.id:
            raise NotFoundError("임시 파일을 찾을 수 없습니다.")

        title = (item.title or temp.original_filename)[:500]

        if item.mode == "NEW_GROUP":
            doc_type = item.document_type_code or temp.document_type_code
            if not doc_type:
                raise ValidationAppError("document_type_code가 필요합니다.")
            self._validate_doc_type(doc_type)
            group = self.repo.create_group(
                person_id=person_id,
                document_type_code=doc_type,
                title=title,
            )
            version_no = 1
        else:
            if item.document_group_id is None:
                raise ValidationAppError("NEW_VERSION에는 document_group_id가 필요합니다.")
            group = self.repo.get_group(item.document_group_id, for_update=True)
            if group is None:
                raise NotFoundError("문서 그룹을 찾을 수 없습니다.")
            if group.person_id != person_id:
                raise ValidationAppError("문서 그룹이 해당 인력에 속하지 않습니다.")
            # Group document_type_code is authoritative; never change it here.
            if (
                item.document_type_code
                and item.document_type_code != group.document_type_code
            ):
                raise ValidationAppError(
                    "NEW_VERSION의 document_type_code가 문서 그룹과 일치하지 않습니다."
                )
            doc_type = group.document_type_code
            version_no = self.repo.max_version_no(group.id) + 1
            self.repo.clear_latest(group.id)

        document_id = uuid4()
        dest_key = self._document_key(person_id, group.id, document_id)
        # S3 has no rename: COPY only here. Temp delete is deferred until COMMIT.
        self.storage.copy(temp.temp_storage_key, dest_key)
        # Track immediately — DB work below may still fail.
        created_permanent_keys.append(dest_key)

        mime = canonical_mime(temp.extension) if temp.extension else temp.mime_type
        doc = self.repo.create_document(
            id=document_id,
            document_group_id=group.id,
            version_no=version_no,
            is_latest=True,
            original_filename=temp.original_filename,
            extension=temp.extension,
            mime_type=mime,
            file_size=temp.file_size,
            storage_key=dest_key,
            sha256=temp.sha256,
            processing_status="UPLOADED",
            uploaded_by=actor_user_id,
        )
        self.repo.delete_temp_file(temp.id)
        self.repo.add_audit(
            action_type="DOCUMENT_CREATE",
            actor_user_id=actor_user_id,
            target_type="DOCUMENT",
            target_id=doc.id,
            after={
                "document_group_id": str(group.id),
                "version_no": version_no,
                "person_id": str(person_id),
                "original_filename": doc.original_filename,
            },
        )
        return doc.id, dest_key, temp.temp_storage_key

    # --- Document queries ---

    def _to_list_item(
        self, doc: Document, group: DocumentGroup, type_name: str | None
    ) -> DocumentListItem:
        error = None
        if doc.processing_status == "FAILED" and doc.processing_error:
            # Short indication only — no stack traces in list/UI.
            error = doc.processing_error.strip().splitlines()[0][:200]
        return DocumentListItem(
            document_id=doc.id,
            document_group_id=group.id,
            document_type_code=group.document_type_code,
            document_type_name=type_name,
            title=group.title,
            version_no=doc.version_no,
            is_latest=doc.is_latest,
            document_date=doc.document_date,
            original_filename=doc.original_filename,
            extension=doc.extension,
            mime_type=doc.mime_type,
            file_size=doc.file_size,
            processing_status=doc.processing_status,
            processing_error=error,
            uploaded_at=doc.uploaded_at,
            deleted_at=doc.deleted_at,
        )

    def list_person_documents(
        self,
        person_id: UUID,
        *,
        is_admin: bool = False,
        include_deleted: bool = False,
    ) -> list[DocumentListItem]:
        person = self.repo.get_person(person_id)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        ensure_person_readable(person, is_admin=is_admin)
        # Soft-deleted documents: default exclude; ADMIN may opt in for restore UX.
        show_deleted = bool(include_deleted and is_admin)
        rows = self.repo.list_person_documents(
            person_id, include_deleted=show_deleted
        )
        return [self._to_list_item(d, g, n) for d, g, n in rows]

    def get_document(
        self, document_id: UUID, *, is_admin: bool = False
    ) -> DocumentDetail:
        doc = self.repo.get_document(
            document_id, include_deleted=is_admin
        )
        if doc is None:
            raise NotFoundError("문서를 찾을 수 없습니다.")
        group = self.repo.get_group(doc.document_group_id, include_deleted=True)
        if group is None:
            raise NotFoundError("문서를 찾을 수 없습니다.")
        person = self.repo.get_person(group.person_id)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        ensure_person_readable(person, is_admin=is_admin)
        if doc.deleted_at is not None and not is_admin:
            raise NotFoundError("문서를 찾을 수 없습니다.")
        code = self.repo.get_code(group.document_type_code)
        item = self._to_list_item(doc, group, code.name if code else None)
        return DocumentDetail(
            **item.model_dump(),
            sha256=doc.sha256,
            preview_storage_key=doc.preview_storage_key,
            preview_page_count=doc.preview_page_count,
            uploaded_by=doc.uploaded_by,
            person_id=group.person_id,
        )

    def list_versions(
        self, group_id: UUID, *, is_admin: bool = False
    ) -> list[DocumentListItem]:
        group = self.repo.get_group(group_id, include_deleted=True)
        if group is None:
            raise NotFoundError("문서 그룹을 찾을 수 없습니다.")
        person = self.repo.get_person(group.person_id)
        if person is None:
            raise NotFoundError("인력을 찾을 수 없습니다.")
        ensure_person_readable(person, is_admin=is_admin)
        rows = self.repo.list_group_versions(group_id, include_deleted=False)
        return [self._to_list_item(d, g, n) for d, g, n in rows]

    def soft_delete(self, document_id: UUID, actor_user_id: UUID) -> None:
        doc = self.repo.get_document(document_id, for_update=True)
        if doc is None:
            raise NotFoundError("문서를 찾을 수 없습니다.")
        group_id = doc.document_group_id
        before = {"deleted_at": None, "is_latest": doc.is_latest, "version_no": doc.version_no}
        self.repo.soft_delete_document(doc)
        self.repo.promote_latest_among_active(group_id)
        self.repo.add_audit(
            action_type="DOCUMENT_DELETE",
            actor_user_id=actor_user_id,
            target_type="DOCUMENT",
            target_id=document_id,
            before=before,
            after={"deleted_at": "set"},
        )
        self.db.commit()

    def restore(self, document_id: UUID, actor_user_id: UUID) -> DocumentDetail:
        doc = self.repo.get_document(
            document_id, for_update=True, include_deleted=True
        )
        if doc is None or doc.deleted_at is None:
            raise NotFoundError("삭제된 문서를 찾을 수 없습니다.")
        self.repo.restore_document(doc)
        self.repo.add_audit(
            action_type="DOCUMENT_RESTORE",
            actor_user_id=actor_user_id,
            target_type="DOCUMENT",
            target_id=document_id,
            after={"deleted_at": None},
        )
        self.db.commit()
        return self.get_document(document_id, is_admin=True)

    def open_download(
        self, document_id: UUID, *, is_admin: bool, actor_user_id: UUID | None
    ) -> tuple[Document, DocumentGroup, StoredObject]:
        detail = self.get_document(document_id, is_admin=is_admin)
        doc = self.repo.get_document(document_id, include_deleted=is_admin)
        assert doc is not None
        group = self.repo.get_group(doc.document_group_id, include_deleted=True)
        assert group is not None
        obj = self.storage.get(doc.storage_key)
        self.repo.add_audit(
            action_type="DOCUMENT_DOWNLOAD",
            actor_user_id=actor_user_id,
            target_type="DOCUMENT",
            target_id=document_id,
            metadata={
                "person_id": str(detail.person_id),
                "original_filename": doc.original_filename,
            },
        )
        self.db.commit()
        return doc, group, obj

    def open_preview(
        self,
        document_id: UUID,
        *,
        is_admin: bool,
        byte_range: tuple[int, int] | None = None,
    ) -> tuple[Document, str, StoredObject, str]:
        """Open preview stream.

        Returns ``(doc, filename, obj, media_type)`` where ``media_type`` is a
        server-chosen safe Content-Type (never client-supplied upload MIME).
        """
        self.get_document(document_id, is_admin=is_admin)
        doc = self.repo.get_document(document_id, include_deleted=is_admin)
        assert doc is not None
        key = doc.preview_storage_key
        if key:
            # Preview objects are generated as PDF in this product design.
            filename = "preview.pdf"
            media_type = "application/pdf"
        else:
            ext = (doc.extension or "").lower()
            if ext not in INLINE_PREVIEW_EXTENSIONS:
                raise PreviewUnavailableError()
            key = doc.storage_key
            filename = doc.original_filename
            media_type = canonical_mime(ext)
        obj = self.storage.get(key, byte_range=byte_range)
        return doc, filename, obj, media_type

    def download_media_type(self, doc: Document) -> str:
        return canonical_mime(doc.extension) if doc.extension else (
            doc.mime_type or "application/octet-stream"
        )

    def parse_range_header(
        self, range_header: str | None, total_size: int
    ) -> tuple[int, int] | None:
        if not range_header:
            return None
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if not match:
            raise ValidationAppError("Range 헤더가 올바르지 않습니다.")
        start_s, end_s = match.groups()
        if start_s == "" and end_s == "":
            raise ValidationAppError("Range 헤더가 올바르지 않습니다.")
        if start_s == "":
            # suffix bytes
            length = int(end_s)
            if length <= 0:
                raise ValidationAppError("Range 헤더가 올바르지 않습니다.")
            start = max(0, total_size - length)
            end = total_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else total_size - 1
        if start > end or start >= total_size:
            raise ValidationAppError("Range 헤더가 올바르지 않습니다.")
        end = min(end, total_size - 1)
        return start, end
