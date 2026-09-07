"""Upload session AI identify orchestration."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.prompts.upload_identity_v1 import SYSTEM_PROMPT, build_user_prompt
from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from app.ai.providers.vlm import OpenAICompatibleVLMProvider, VLMProvider
from app.ai.schemas.identity import IdentityExtraction
from app.core.config import Settings, get_settings
from app.modules.document_processing.converters.base import PdfConverter
from app.modules.upload_identification.duplicate_matcher import DuplicateMatcher
from app.modules.upload_identification.repository import UploadIdentificationRepository
from app.modules.upload_identification.source_extractor import IdentitySourceExtractor
from app.storage.base import ObjectStorage
from app.storage.s3 import get_object_storage

logger = logging.getLogger(__name__)


class IdentificationService:
    def __init__(
        self,
        db: Session,
        *,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        vlm: VLMProvider | None = None,
        converter: PdfConverter | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.storage = storage or get_object_storage()
        self.repo = UploadIdentificationRepository(db)
        self.llm = llm or OpenAICompatibleLLMProvider(self.settings)
        self.vlm = vlm or OpenAICompatibleVLMProvider(self.settings)
        self.extractor = IdentitySourceExtractor(
            self.storage,
            settings=self.settings,
            converter=converter,
            vlm=self.vlm,
        )
        self.matcher = DuplicateMatcher(db)

    def run(self, session_id: UUID) -> str:
        """Execute identify for a session already in IDENTIFYING.

        Returns the final status written (or discarded terminal status).
        """
        session = self.repo.get_session(session_id, for_update=True)
        if session is None:
            logger.info("identify skip missing session_id=%s", session_id)
            return "NOT_FOUND"
        if session.status in {"CANCELLED", "RESOLVED", "EXPIRED"}:
            logger.info(
                "identify discard terminal status=%s session_id=%s",
                session.status,
                session_id,
            )
            return session.status
        if session.status != "IDENTIFYING":
            logger.info(
                "identify skip unexpected status=%s session_id=%s",
                session.status,
                session_id,
            )
            return session.status

        # Snapshot prior identity for failure restore (re-identify case).
        prior_name = session.identified_name
        prior_company = session.identified_company
        prior_phone = session.identified_phone
        prior_email = session.identified_email
        prior_duplicates = list(session.duplicate_result_json or [])
        had_prior_identity = any(
            [prior_name, prior_company, prior_phone, prior_email]
        )

        try:
            files = self.repo.list_temp_files(session_id)
            if not files:
                raise AIProviderError("no temp files to identify")

            bundle = self.extractor.extract_session_files(
                files,
                log_context={"upload_session_id": str(session_id)},
            )
            usable = bundle.usable_sources
            if not usable:
                raise AIProviderError("all temp files failed extraction")

            blocks = bundle.combined_document_blocks(
                int(self.settings.identify_max_text_chars)
            )
            if not blocks.strip():
                raise AIProviderError("no usable text for identity extraction")

            identity = self.llm.extract_identity(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(blocks),
                log_context={
                    "upload_session_id": str(session_id),
                    "file_count": len(files),
                    "usable_file_count": len(usable),
                    "vlm_pages": bundle.total_vlm_pages,
                },
            )
            if not isinstance(identity, IdentityExtraction):
                raise AIResponseValidationError("invalid identity type")
            if not identity.has_any_signal():
                raise AIResponseValidationError("empty identity extraction")

            candidates = self.matcher.find_candidates(identity)
            dup_json = [c.to_dict() for c in candidates]

            # Re-check status before writing results (cancel race).
            self.db.refresh(session)
            session = self.repo.get_session(session_id, for_update=True)
            if session is None or session.status != "IDENTIFYING":
                logger.info(
                    "identify discard after work status=%s session_id=%s",
                    None if session is None else session.status,
                    session_id,
                )
                self.db.rollback()
                return "CANCELLED" if session is None else session.status

            self.repo.save_identified(
                session,
                name=identity.name,
                company=identity.company,
                phone=identity.phone,
                email=identity.email,
                duplicates=dup_json,
            )
            self.repo.add_audit(
                action_type="UPLOAD_SESSION_IDENTIFY",
                actor_user_id=session.created_by,
                session_id=session_id,
                after={
                    "status": "IDENTIFIED",
                    "has_name": bool(identity.name),
                    "candidate_count": len(dup_json),
                },
                metadata={"source": "UPLOAD_IDENTIFY"},
            )
            self.db.commit()
            return "IDENTIFIED"
        except Exception:
            logger.exception("identify failed session_id=%s", session_id)
            self.db.rollback()
            return self._fail_restore(
                session_id,
                had_prior_identity=had_prior_identity,
                prior_name=prior_name,
                prior_company=prior_company,
                prior_phone=prior_phone,
                prior_email=prior_email,
                prior_duplicates=prior_duplicates,
            )

    def _fail_restore(
        self,
        session_id: UUID,
        *,
        had_prior_identity: bool,
        prior_name: str | None,
        prior_company: str | None,
        prior_phone: str | None,
        prior_email: str | None,
        prior_duplicates: list,
    ) -> str:
        session = self.repo.get_session(session_id, for_update=True)
        if session is None:
            return "NOT_FOUND"
        if session.status != "IDENTIFYING":
            # Cancel / resolve won the race — do not overwrite.
            return session.status

        if had_prior_identity:
            session.status = "IDENTIFIED"
            session.identified_name = prior_name
            session.identified_company = prior_company
            session.identified_phone = prior_phone
            session.identified_email = prior_email
            session.duplicate_result_json = prior_duplicates
            restore_to = "IDENTIFIED"
        else:
            session.status = "UPLOADING"
            # Keep null identity fields for initial failure.
            restore_to = "UPLOADING"

        self.db.add(session)
        self.repo.add_audit(
            action_type="UPLOAD_SESSION_IDENTIFY_FAILED",
            actor_user_id=session.created_by,
            session_id=session_id,
            after={"status": restore_to},
            metadata={"source": "UPLOAD_IDENTIFY"},
        )
        self.db.commit()
        return restore_to
