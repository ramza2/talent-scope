"""Search index persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from app.db.models.person import PersonProfile
from app.db.models.search import SearchIndexItem, SearchIndexJob
from app.modules.search.schemas import SearchDocument


class SearchRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_job(self, job_id: UUID) -> SearchIndexJob | None:
        return self.db.get(SearchIndexJob, job_id)

    def claim_pending_job(self, job_id: UUID) -> SearchIndexJob | None:
        """Atomically claim a PENDING job. Returns None if already claimed/finished."""
        now = datetime.now(timezone.utc)
        stmt = (
            update(SearchIndexJob)
            .where(
                SearchIndexJob.id == job_id,
                SearchIndexJob.status == "PENDING",
            )
            .values(
                status="PROCESSING",
                started_at=now,
                error_message=None,
            )
            .returning(SearchIndexJob.id)
        )
        result = self.db.execute(stmt)
        if result.first() is None:
            return None
        self.db.flush()
        return self.get_job(job_id)

    def list_pending_job_ids(self, *, limit: int = 50) -> list[UUID]:
        stmt: Select[tuple[UUID]] = (
            select(SearchIndexJob.id)
            .where(SearchIndexJob.status == "PENDING")
            .order_by(SearchIndexJob.created_at.asc(), SearchIndexJob.id.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def mark_job_completed(self, job: SearchIndexJob) -> SearchIndexJob:
        job.status = "COMPLETED"
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = None
        self.db.add(job)
        self.db.flush()
        return job

    def mark_job_failed_if_pending(
        self,
        job_id: UUID,
        *,
        error_message: str,
    ) -> SearchIndexJob | None:
        """Mark FAILED when job is still PENDING/PROCESSING after rollback.

        Single-TX claim: hard-kill before COMMIT leaves PENDING. Handled exceptions
        roll back the claim, then this helper records FAILED.
        """
        now = datetime.now(timezone.utc)
        safe = (error_message or "unknown error")[:500]
        stmt = (
            update(SearchIndexJob)
            .where(
                SearchIndexJob.id == job_id,
                SearchIndexJob.status.in_(("PENDING", "PROCESSING")),
            )
            .values(
                status="FAILED",
                completed_at=now,
                error_message=safe,
                retry_count=SearchIndexJob.retry_count + 1,
            )
            .returning(SearchIndexJob.id)
        )
        result = self.db.execute(stmt)
        if result.first() is None:
            return None
        self.db.flush()
        return self.get_job(job_id)

    def mark_unsupported_action_failed(
        self,
        job: SearchIndexJob,
        *,
        error_message: str,
    ) -> SearchIndexJob:
        job.status = "FAILED"
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = (error_message or "unsupported action")[:500]
        job.retry_count = int(job.retry_count or 0) + 1
        self.db.add(job)
        self.db.flush()
        return job

    def list_active_items_for_person(
        self,
        person_id: UUID,
        *,
        object_types: tuple[str, ...] = ("PROFILE", "PROJECT"),
    ) -> list[SearchIndexItem]:
        stmt = (
            select(SearchIndexItem)
            .where(
                SearchIndexItem.person_id == person_id,
                SearchIndexItem.is_active.is_(True),
                SearchIndexItem.object_type.in_(object_types),
            )
            .order_by(
                SearchIndexItem.object_type.asc(),
                SearchIndexItem.object_id.asc(),
                SearchIndexItem.created_at.asc(),
                SearchIndexItem.id.asc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def upsert_search_document(self, document: SearchDocument) -> SearchIndexItem:
        """Upsert PROFILE/PROJECT item; preserve embedding when text+version unchanged."""
        active_rows = self._active_rows_for_object(
            object_type=document.object_type,
            object_id=document.object_id,
        )
        canonical: SearchIndexItem | None = None
        if active_rows:
            canonical = active_rows[0]
            for extra in active_rows[1:]:
                extra.is_active = False
                self.db.add(extra)

        now = datetime.now(timezone.utc)
        meta = dict(document.metadata)
        version = str(meta.get("search_document_version") or "")

        if canonical is None:
            item = SearchIndexItem(
                id=uuid4(),
                person_id=document.person_id,
                object_type=document.object_type,
                object_id=document.object_id,
                search_text=document.search_text,
                embedding=None,
                source_weight=document.source_weight,
                metadata_json=meta,
                embedding_model=None,
                embedding_version=None,
                is_active=True,
                indexed_at=now,
            )
            self.db.add(item)
            self.db.flush()
            return item

        prev_meta = (
            canonical.metadata_json if isinstance(canonical.metadata_json, dict) else {}
        )
        prev_version = str(prev_meta.get("search_document_version") or "")
        same_text = (canonical.search_text or "") == document.search_text
        same_version = prev_version == version and version != ""

        canonical.person_id = document.person_id
        canonical.search_text = document.search_text
        canonical.source_weight = document.source_weight
        canonical.metadata_json = meta
        canonical.is_active = True
        canonical.indexed_at = now

        if not (same_text and same_version):
            canonical.embedding = None
            canonical.embedding_model = None
            canonical.embedding_version = None

        self.db.add(canonical)
        self.db.flush()
        return canonical

    def deactivate_items(self, items: list[SearchIndexItem]) -> int:
        count = 0
        for item in items:
            if item.is_active:
                item.is_active = False
                self.db.add(item)
                count += 1
        if count:
            self.db.flush()
        return count

    def deactivate_all_profile_project_for_person(self, person_id: UUID) -> int:
        return self.deactivate_items(self.list_active_items_for_person(person_id))

    def _active_rows_for_object(
        self,
        *,
        object_type: str,
        object_id: UUID,
    ) -> list[SearchIndexItem]:
        stmt = (
            select(SearchIndexItem)
            .where(
                SearchIndexItem.object_type == object_type,
                SearchIndexItem.object_id == object_id,
                SearchIndexItem.is_active.is_(True),
            )
            .order_by(SearchIndexItem.created_at.asc(), SearchIndexItem.id.asc())
        )
        return list(self.db.scalars(stmt).all())


def lock_person_profile_for_update(db: Session, person_id: UUID) -> PersonProfile | None:
    """Lock PersonProfile row for a consistent Confirmed snapshot during rebuild."""
    stmt = (
        select(PersonProfile)
        .where(PersonProfile.person_id == person_id)
        .with_for_update()
    )
    return db.scalars(stmt).first()
