"""Batch read-only evidence lookups for search result enrichment."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from app.db.models.document import Document, DocumentChunk, DocumentGroup
from app.db.models.evidence import Evidence, EvidenceLink
from app.modules.evidence.materializer import RELATION_TYPE_SUPPORTS
from app.modules.search.ranking import SEARCH_EVIDENCE_SNIPPET_MAX_CHARS


@dataclass(frozen=True)
class EvidenceTargetKey:
    target_type: str
    target_id: UUID
    field_name: str | None = None


@dataclass(frozen=True)
class EvidenceRow:
    evidence_id: UUID
    target_type: str
    target_id: UUID
    field_name: str | None
    relation_type: str
    document_id: UUID
    document_title: str | None
    original_filename: str | None
    version_no: int | None
    page_no: int | None
    quote_text: str | None
    person_id: UUID


@dataclass(frozen=True)
class DocumentChunkRow:
    chunk_id: UUID
    person_id: UUID
    document_id: UUID
    document_title: str | None
    original_filename: str | None
    version_no: int | None
    page_no: int | None
    chunk_text: str


def clip_snippet(text: str | None, *, max_chars: int = SEARCH_EVIDENCE_SNIPPET_MAX_CHARS) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 1:
        return cleaned[:max_chars]
    return cleaned[: max_chars - 1].rstrip() + "…"


class SearchEvidenceRepository:
    """Read-only batch evidence / chunk helpers for search responses."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_supporting_evidence(
        self,
        targets: Sequence[EvidenceTargetKey],
        *,
        person_ids: Sequence[UUID],
    ) -> list[EvidenceRow]:
        if not targets or not person_ids:
            return []

        person_id_set = set(person_ids)
        clauses = []
        for target in targets:
            cond = and_(
                EvidenceLink.target_type == target.target_type,
                EvidenceLink.target_id == target.target_id,
            )
            if target.field_name is None:
                cond = and_(cond, EvidenceLink.field_name.is_(None))
            else:
                cond = and_(cond, EvidenceLink.field_name == target.field_name)
            clauses.append(cond)

        stmt = (
            select(
                Evidence.id,
                EvidenceLink.target_type,
                EvidenceLink.target_id,
                EvidenceLink.field_name,
                EvidenceLink.relation_type,
                Evidence.document_id,
                DocumentGroup.title,
                Document.original_filename,
                Document.version_no,
                Evidence.page_no,
                Evidence.quote_text,
                DocumentGroup.person_id,
            )
            .join(Evidence, Evidence.id == EvidenceLink.evidence_id)
            .join(Document, Document.id == Evidence.document_id)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .where(
                EvidenceLink.relation_type == RELATION_TYPE_SUPPORTS,
                Document.deleted_at.is_(None),
                DocumentGroup.deleted_at.is_(None),
                Document.processing_status == "READY",
                DocumentGroup.person_id.in_(list(person_id_set)),
                or_(*clauses),
            )
            .order_by(
                Evidence.page_no.asc().nulls_last(),
                Evidence.created_at.asc(),
                Evidence.id.asc(),
            )
        )
        rows = self.db.execute(stmt).all()
        out: list[EvidenceRow] = []
        for row in rows:
            out.append(
                EvidenceRow(
                    evidence_id=row[0],
                    target_type=row[1],
                    target_id=row[2],
                    field_name=row[3],
                    relation_type=row[4],
                    document_id=row[5],
                    document_title=row[6],
                    original_filename=row[7],
                    version_no=row[8],
                    page_no=row[9],
                    quote_text=row[10],
                    person_id=row[11],
                )
            )
        return out

    def list_document_chunks(
        self,
        chunk_ids: Sequence[UUID],
        *,
        person_ids: Sequence[UUID],
    ) -> list[DocumentChunkRow]:
        if not chunk_ids or not person_ids:
            return []
        stmt = (
            select(
                DocumentChunk.id,
                DocumentGroup.person_id,
                Document.id,
                DocumentGroup.title,
                Document.original_filename,
                Document.version_no,
                DocumentChunk.page_from,
                DocumentChunk.chunk_text,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .where(
                DocumentChunk.id.in_(list(chunk_ids)),
                DocumentGroup.person_id.in_(list(person_ids)),
                Document.deleted_at.is_(None),
                DocumentGroup.deleted_at.is_(None),
                Document.processing_status == "READY",
            )
            .order_by(DocumentChunk.page_from.asc().nulls_last(), DocumentChunk.id.asc())
        )
        rows = self.db.execute(stmt).all()
        return [
            DocumentChunkRow(
                chunk_id=row[0],
                person_id=row[1],
                document_id=row[2],
                document_title=row[3],
                original_filename=row[4],
                version_no=row[5],
                page_no=row[6],
                chunk_text=row[7] or "",
            )
            for row in rows
        ]
