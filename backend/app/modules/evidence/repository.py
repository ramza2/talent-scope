"""Evidence DB access."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffEvidence, AnalysisRunDocument
from app.db.models.document import Document, DocumentGroup, DocumentPage
from app.db.models.evidence import Evidence, EvidenceLink
from app.db.models.person import Person


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_run_document_ids(self, analysis_run_id: UUID) -> set[UUID]:
        rows = self.db.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == analysis_run_id
            )
        ).scalars().all()
        return set(rows)

    def get_page(
        self, document_id: UUID, page_no: int
    ) -> DocumentPage | None:
        return self.db.execute(
            select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_no == page_no,
            )
        ).scalar_one_or_none()

    def find_exact_evidence(
        self,
        *,
        document_id: UUID,
        document_page_id: UUID | None,
        page_no: int | None,
        quote_text: str,
        char_start: int | None,
        char_end: int | None,
        extraction_method: str | None,
    ) -> Evidence | None:
        stmt = select(Evidence).where(
            Evidence.document_id == document_id,
            Evidence.quote_text == quote_text,
            Evidence.page_no == page_no,
            Evidence.document_page_id == document_page_id,
            Evidence.char_start == char_start,
            Evidence.char_end == char_end,
            Evidence.extraction_method == extraction_method,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def add_evidence(self, row: Evidence) -> Evidence:
        self.db.add(row)
        self.db.flush()
        return row

    def add_diff_evidence(self, *, diff_item_id: UUID, evidence_id: UUID) -> None:
        exists = self.db.execute(
            select(AnalysisDiffEvidence).where(
                AnalysisDiffEvidence.diff_item_id == diff_item_id,
                AnalysisDiffEvidence.evidence_id == evidence_id,
            )
        ).scalar_one_or_none()
        if exists is not None:
            return
        self.db.add(
            AnalysisDiffEvidence(diff_item_id=diff_item_id, evidence_id=evidence_id)
        )

    def add_link(self, row: EvidenceLink) -> EvidenceLink:
        self.db.add(row)
        self.db.flush()
        return row

    def find_link(
        self,
        *,
        evidence_id: UUID,
        target_type: str,
        target_id: UUID,
        field_name: str | None,
        relation_type: str,
    ) -> EvidenceLink | None:
        stmt = select(EvidenceLink).where(
            EvidenceLink.evidence_id == evidence_id,
            EvidenceLink.target_type == target_type,
            EvidenceLink.target_id == target_id,
            EvidenceLink.relation_type == relation_type,
        )
        if field_name is None:
            stmt = stmt.where(EvidenceLink.field_name.is_(None))
        else:
            stmt = stmt.where(EvidenceLink.field_name == field_name)
        return self.db.execute(stmt).scalar_one_or_none()

    def list_evidence_by_diff_ids(
        self, diff_ids: list[UUID]
    ) -> dict[UUID, list[Evidence]]:
        if not diff_ids:
            return {}
        rows = self.db.execute(
            select(AnalysisDiffEvidence, Evidence)
            .join(Evidence, Evidence.id == AnalysisDiffEvidence.evidence_id)
            .where(AnalysisDiffEvidence.diff_item_id.in_(diff_ids))
            .order_by(Evidence.page_no.nulls_last(), Evidence.created_at, Evidence.id)
        ).all()
        out: dict[UUID, list[Evidence]] = {diff_id: [] for diff_id in diff_ids}
        for link, evidence in rows:
            out.setdefault(link.diff_item_id, []).append(evidence)
        return out

    def get_evidence(self, evidence_id: UUID) -> Evidence | None:
        return self.db.execute(
            select(Evidence).where(Evidence.id == evidence_id)
        ).scalar_one_or_none()

    def get_document_with_group(
        self, document_id: UUID
    ) -> tuple[Document, DocumentGroup] | None:
        row = self.db.execute(
            select(Document, DocumentGroup)
            .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
            .where(Document.id == document_id)
        ).first()
        if row is None:
            return None
        return row[0], row[1]

    def get_person(self, person_id: UUID) -> Person | None:
        return self.db.execute(
            select(Person).where(Person.id == person_id)
        ).scalar_one_or_none()

    def list_links_for_evidence(self, evidence_id: UUID) -> list[EvidenceLink]:
        return list(
            self.db.execute(
                select(EvidenceLink)
                .where(EvidenceLink.evidence_id == evidence_id)
                .order_by(
                    EvidenceLink.target_type,
                    EvidenceLink.field_name.nulls_first(),
                    EvidenceLink.created_at,
                    EvidenceLink.id,
                )
            ).scalars().all()
        )

    def list_links_for_target(
        self,
        *,
        target_type: str,
        target_id: UUID,
        field_name: str | None = None,
    ) -> list[tuple[EvidenceLink, Evidence]]:
        stmt = (
            select(EvidenceLink, Evidence)
            .join(Evidence, Evidence.id == EvidenceLink.evidence_id)
            .where(
                EvidenceLink.target_type == target_type,
                EvidenceLink.target_id == target_id,
            )
        )
        if field_name is not None:
            stmt = stmt.where(EvidenceLink.field_name == field_name)
        stmt = stmt.order_by(
            Evidence.page_no.nulls_last(),
            Evidence.created_at,
            Evidence.id,
            EvidenceLink.id,
        )
        return list(self.db.execute(stmt).all())
