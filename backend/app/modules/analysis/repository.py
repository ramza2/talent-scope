"""Persistence helpers for profile analysis runs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
from app.db.models.code import CodeMaster
from app.db.models.document import Document, DocumentGroup, DocumentPage
from app.db.models.person import Person, PersonProfile
from app.db.models.project import Project
from app.db.models.revision import AuditLog, ProfileRevision


class AnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def try_claim_analysis(self, run_id: UUID) -> AnalysisRun | None:
        """Atomic QUEUED → PROCESSING claim. Returns claimed row or None."""
        now = datetime.now(UTC)
        row = self.db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.id == run_id, AnalysisRun.status == "QUEUED")
            .values(
                status="PROCESSING",
                started_at=now,
                error_message=None,
                updated_at=now,
            )
            .returning(AnalysisRun)
        ).scalar_one_or_none()
        if row is not None:
            self.db.flush()
        return row

    def create_run(
        self,
        *,
        person_id: UUID,
        base_profile_version: int,
        llm_model: str | None,
        vlm_model: str | None,
        prompt_version: str,
        schema_version: str,
    ) -> AnalysisRun:
        run = AnalysisRun(
            person_id=person_id,
            status="QUEUED",
            candidate_json={},
            base_profile_version=base_profile_version,
            llm_model=llm_model,
            vlm_model=vlm_model,
            prompt_version=prompt_version,
            schema_version=schema_version,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def add_run_documents(
        self,
        run_id: UUID,
        document_ids: list[UUID],
        *,
        analysis_role: str | None = "SOURCE",
    ) -> None:
        for doc_id in document_ids:
            self.db.add(
                AnalysisRunDocument(
                    analysis_run_id=run_id,
                    document_id=doc_id,
                    analysis_role=analysis_role,
                )
            )
        self.db.flush()

    def get_run(
        self, run_id: UUID, *, for_update: bool = False
    ) -> AnalysisRun | None:
        stmt = select(AnalysisRun).where(AnalysisRun.id == run_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def list_run_document_ids(self, run_id: UUID) -> list[UUID]:
        rows = self.db.execute(
            select(AnalysisRunDocument.document_id).where(
                AnalysisRunDocument.analysis_run_id == run_id
            )
        ).scalars().all()
        return list(rows)

    def list_runs(
        self,
        *,
        status: str | None,
        person_id: UUID | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[list[AnalysisRun], int]:
        filters = []
        if status:
            filters.append(AnalysisRun.status == status)
        if person_id is not None:
            filters.append(AnalysisRun.person_id == person_id)

        total = int(
            self.db.execute(
                select(func.count())
                .select_from(AnalysisRun)
                .where(*filters)
            ).scalar_one()
        )

        order = AnalysisRun.created_at.desc()
        if sort == "completed_desc":
            order = AnalysisRun.completed_at.desc().nullslast()
        elif sort == "created_asc":
            order = AnalysisRun.created_at.asc()
        elif sort == "started_desc":
            order = AnalysisRun.started_at.desc().nullslast()

        rows = list(
            self.db.execute(
                select(AnalysisRun)
                .where(*filters)
                .order_by(order)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            .scalars()
            .all()
        )
        return rows, total

    def replace_diffs(
        self, run_id: UUID, specs: list[dict[str, Any]]
    ) -> list[AnalysisDiffItem]:
        self.db.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id == run_id)
        )
        items: list[AnalysisDiffItem] = []
        for spec in specs:
            confidence = spec.get("confidence")
            if confidence is not None and not isinstance(confidence, Decimal):
                confidence = Decimal(str(confidence))
            item = AnalysisDiffItem(
                analysis_run_id=run_id,
                entity_type=spec["entity_type"],
                candidate_path=spec.get("candidate_path"),
                existing_target_id=spec.get("existing_target_id"),
                field_name=spec.get("field_name"),
                change_type=spec["change_type"],
                old_value=spec.get("old_value"),
                new_value=spec.get("new_value"),
                confidence=confidence,
                evidence_type=spec.get("evidence_type"),
                review_status=spec.get("review_status", "PENDING"),
            )
            self.db.add(item)
            items.append(item)
        self.db.flush()
        return items

    def clear_candidate_and_diffs(self, run: AnalysisRun) -> None:
        self.db.execute(
            delete(AnalysisDiffItem).where(
                AnalysisDiffItem.analysis_run_id == run.id
            )
        )
        run.candidate_json = {}
        run.overall_confidence = None
        run.completed_at = None
        run.started_at = None
        run.error_message = None
        run.confirmed_at = None
        run.confirmed_by = None
        self.db.add(run)
        self.db.flush()

    def get_diff(
        self, diff_id: UUID, *, for_update: bool = False
    ) -> AnalysisDiffItem | None:
        stmt = select(AnalysisDiffItem).where(AnalysisDiffItem.id == diff_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def list_diffs(
        self,
        run_id: UUID,
        *,
        change_types: list[str] | None = None,
        review_status: str | None = None,
        entity_type: str | None = None,
    ) -> list[AnalysisDiffItem]:
        stmt = select(AnalysisDiffItem).where(
            AnalysisDiffItem.analysis_run_id == run_id
        )
        if change_types:
            stmt = stmt.where(AnalysisDiffItem.change_type.in_(change_types))
        if review_status:
            stmt = stmt.where(AnalysisDiffItem.review_status == review_status)
        if entity_type:
            stmt = stmt.where(AnalysisDiffItem.entity_type == entity_type)
        stmt = stmt.order_by(
            AnalysisDiffItem.entity_type.asc(),
            AnalysisDiffItem.candidate_path.asc().nullslast(),
            AnalysisDiffItem.created_at.asc(),
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_diffs(self, run_id: UUID) -> dict[str, int]:
        """Return counts keyed by change_type lowercase + pending review_status."""
        change_rows = self.db.execute(
            select(AnalysisDiffItem.change_type, func.count())
            .where(AnalysisDiffItem.analysis_run_id == run_id)
            .group_by(AnalysisDiffItem.change_type)
        ).all()
        pending = int(
            self.db.execute(
                select(func.count())
                .select_from(AnalysisDiffItem)
                .where(
                    AnalysisDiffItem.analysis_run_id == run_id,
                    AnalysisDiffItem.review_status == "PENDING",
                )
            ).scalar_one()
        )
        counts = {
            "same": 0,
            "new": 0,
            "update": 0,
            "conflict": 0,
            "review": 0,
            "pending": pending,
        }
        for change_type, count in change_rows:
            key = "update" if change_type == "UPDATE" else change_type.lower()
            if key in counts:
                counts[key] = int(count)
        return counts

    def get_profile_revision_snapshot(
        self, person_id: UUID, revision_no: int
    ) -> dict[str, Any] | None:
        row = self.db.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == person_id,
                ProfileRevision.revision_no == revision_no,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        snap = row.snapshot_json
        return dict(snap) if isinstance(snap, dict) else None

    def get_person(
        self, person_id: UUID, *, for_update: bool = False
    ) -> Person | None:
        stmt = select(Person).where(Person.id == person_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def get_profile(
        self, person_id: UUID, *, for_update: bool = False
    ) -> PersonProfile | None:
        stmt = select(PersonProfile).where(PersonProfile.person_id == person_id)
        if for_update:
            stmt = stmt.with_for_update()
        return self.db.execute(stmt).scalar_one_or_none()

    def list_documents_by_ids(
        self, document_ids: list[UUID]
    ) -> list[tuple[Document, DocumentGroup, str | None]]:
        if not document_ids:
            return []
        rows = list(
            self.db.execute(
                select(Document, DocumentGroup, CodeMaster.name)
                .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
                .outerjoin(
                    CodeMaster, CodeMaster.code == DocumentGroup.document_type_code
                )
                .where(
                    Document.id.in_(document_ids),
                    Document.deleted_at.is_(None),
                    DocumentGroup.deleted_at.is_(None),
                )
            ).all()
        )
        by_id = {doc.id: (doc, group, name) for doc, group, name in rows}
        return [by_id[doc_id] for doc_id in document_ids if doc_id in by_id]

    def list_document_pages(self, document_id: UUID) -> list[DocumentPage]:
        return list(
            self.db.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_no.asc())
            )
            .scalars()
            .all()
        )

    def get_project(
        self, project_id: UUID, *, person_id: UUID | None = None
    ) -> Project | None:
        stmt = select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
        if person_id is not None:
            stmt = stmt.where(Project.person_id == person_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def add_audit(
        self,
        *,
        action_type: str,
        actor_user_id: UUID | None,
        target_type: str = "ANALYSIS_RUN",
        target_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
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

    def mark_failed(self, run: AnalysisRun, error_message: str) -> None:
        now = datetime.now(UTC)
        run.status = "FAILED"
        run.error_message = (error_message or "analysis failed")[:4000]
        run.completed_at = now
        run.updated_at = now
        self.db.add(run)
        self.db.flush()

    def mark_reviewing(
        self,
        run: AnalysisRun,
        *,
        candidate_json: dict[str, Any],
        overall_confidence: Decimal | float | None = None,
    ) -> None:
        now = datetime.now(UTC)
        run.status = "REVIEWING"
        run.candidate_json = candidate_json
        run.error_message = None
        run.completed_at = now
        run.updated_at = now
        if overall_confidence is not None:
            run.overall_confidence = (
                overall_confidence
                if isinstance(overall_confidence, Decimal)
                else Decimal(str(overall_confidence))
            )
        self.db.add(run)
        self.db.flush()

    def mark_queued(self, run: AnalysisRun) -> None:
        now = datetime.now(UTC)
        run.status = "QUEUED"
        run.error_message = None
        run.started_at = None
        run.completed_at = None
        run.updated_at = now
        self.db.add(run)
        self.db.flush()

    def list_active_codes(
        self, code_types: list[str] | None = None
    ) -> list[CodeMaster]:
        types = code_types or ["JOB", "TECH", "EXP", "BIZ", "CUSTOMER_TYPE"]
        return list(
            self.db.execute(
                select(CodeMaster)
                .where(
                    CodeMaster.code_type.in_(types),
                    CodeMaster.is_active.is_(True),
                )
                .order_by(
                    CodeMaster.code_type.asc(),
                    CodeMaster.sort_order.asc(),
                    CodeMaster.code.asc(),
                )
            )
            .scalars()
            .all()
        )
