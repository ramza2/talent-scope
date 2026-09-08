"""Evidence query service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, ValidationAppError
from app.modules.evidence.repository import EvidenceRepository
from app.modules.evidence.schemas import (
    EVIDENCE_TARGET_TYPES,
    EvidenceDetail,
    EvidenceDocumentBrief,
    EvidenceLinkItem,
    EvidenceListItem,
)
from app.modules.people.visibility import ensure_person_readable


class EvidenceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = EvidenceRepository(db)

    def get_evidence(self, evidence_id: UUID, *, is_admin: bool) -> EvidenceDetail:
        evidence = self.repo.get_evidence(evidence_id)
        if evidence is None:
            raise NotFoundError("근거를 찾을 수 없습니다.")
        doc_pair = self.repo.get_document_with_group(evidence.document_id)
        if doc_pair is None:
            raise NotFoundError("근거를 찾을 수 없습니다.")
        document, group = doc_pair
        if document.deleted_at is not None or group.deleted_at is not None:
            if not is_admin:
                raise NotFoundError("근거를 찾을 수 없습니다.")
        person = self.repo.get_person(group.person_id)
        if person is None:
            raise NotFoundError("근거를 찾을 수 없습니다.")
        ensure_person_readable(person, is_admin=is_admin)

        links = [
            EvidenceLinkItem(
                target_type=link.target_type,
                target_id=link.target_id,
                field_name=link.field_name,
                relation_type=link.relation_type,
            )
            for link in self.repo.list_links_for_evidence(evidence.id)
        ]
        return EvidenceDetail(
            id=evidence.id,
            document=_document_brief(document, group),
            page_no=evidence.page_no,
            quote_text=evidence.quote_text,
            bbox=evidence.bbox_json,
            char_start=evidence.char_start,
            char_end=evidence.char_end,
            extraction_method=evidence.extraction_method,
            created_at=evidence.created_at,
            links=links,
        )

    def list_for_target(
        self,
        *,
        target_type: str,
        target_id: UUID,
        field_name: str | None,
        is_admin: bool,
    ) -> list[EvidenceListItem]:
        if target_type not in EVIDENCE_TARGET_TYPES:
            raise ValidationAppError(f"허용되지 않은 target_type입니다: {target_type}")

        rows = self.repo.list_links_for_target(
            target_type=target_type,
            target_id=target_id,
            field_name=field_name,
        )
        out: list[EvidenceListItem] = []
        for link, evidence in rows:
            doc_pair = self.repo.get_document_with_group(evidence.document_id)
            if doc_pair is None:
                continue
            document, group = doc_pair
            if document.deleted_at is not None or group.deleted_at is not None:
                if not is_admin:
                    continue
            person = self.repo.get_person(group.person_id)
            if person is None:
                continue
            try:
                ensure_person_readable(person, is_admin=is_admin)
            except NotFoundError:
                continue
            out.append(
                EvidenceListItem(
                    id=evidence.id,
                    document=_document_brief(document, group),
                    page_no=evidence.page_no,
                    quote_text=evidence.quote_text,
                    bbox=evidence.bbox_json,
                    char_start=evidence.char_start,
                    char_end=evidence.char_end,
                    extraction_method=evidence.extraction_method,
                    relation_type=link.relation_type,
                )
            )
        return out


def _document_brief(document, group) -> EvidenceDocumentBrief:
    title = group.title or document.original_filename
    return EvidenceDocumentBrief(
        id=document.id,
        title=title,
        original_filename=document.original_filename,
        version_no=document.version_no,
    )
