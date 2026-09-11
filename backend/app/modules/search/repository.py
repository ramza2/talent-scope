"""Search index persistence helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.person import PersonProfile
from app.db.models.search import SearchIndexItem, SearchIndexJob
from app.modules.search.schemas import SearchDocument

TERMINAL_JOB_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


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
                completed_at=None,
            )
            .returning(SearchIndexJob.id)
        )
        result = self.db.execute(stmt)
        if result.first() is None:
            return None
        self.db.flush()
        return self.get_job(job_id)


    def reserve_pending_jobs(self, *, limit: int = 50) -> list[SearchIndexJob]:
        """Atomically reserve PENDING jobs for dispatch (FOR UPDATE SKIP LOCKED).

        Sets status=PROCESSING and started_at. Caller must COMMIT before Celery publish.
        Concurrent dispatchers never reserve the same job.
        """
        if limit <= 0:
            return []
        stmt = (
            select(SearchIndexJob)
            .where(SearchIndexJob.status == "PENDING")
            .order_by(SearchIndexJob.created_at.asc(), SearchIndexJob.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(self.db.scalars(stmt).all())
        now = datetime.now(timezone.utc)
        for job in jobs:
            job.status = "PROCESSING"
            job.started_at = now
            job.error_message = None
            job.completed_at = None
            self.db.add(job)
        if jobs:
            self.db.flush()
        return jobs

    def release_reserved_job(self, job_id: UUID) -> bool:
        """Conditional PROCESSING → PENDING after Celery publish failure.

        Never reverts COMPLETED / FAILED / CANCELLED.
        """
        stmt = (
            update(SearchIndexJob)
            .where(
                SearchIndexJob.id == job_id,
                SearchIndexJob.status == "PROCESSING",
            )
            .values(
                status="PENDING",
                started_at=None,
                completed_at=None,
                error_message=None,
            )
            .returning(SearchIndexJob.id)
        )
        result = self.db.execute(stmt)
        released = result.first() is not None
        if released:
            self.db.flush()
        return released

    def lock_processing_job(self, job_id: UUID) -> SearchIndexJob | None:
        """Exclusive lock on a PROCESSING job row (SKIP LOCKED).

        Returns None when another worker holds the lock or status != PROCESSING.
        """
        stmt = (
            select(SearchIndexJob)
            .where(
                SearchIndexJob.id == job_id,
                SearchIndexJob.status == "PROCESSING",
            )
            .with_for_update(skip_locked=True)
        )
        return self.db.scalars(stmt).first()

    def recover_stale_processing_jobs(
        self,
        *,
        older_than: datetime,
        limit: int = 100,
        embed_older_than: datetime | None = None,
    ) -> list[UUID]:
        """Return stale PROCESSING jobs to PENDING (dispatcher can re-publish).

        Policy: retry_count += 1 so hard-kill / stuck-dispatch cycles are visible.
        Embedding UPSERT jobs use a longer timeout when embed_older_than is set.
        """
        if limit <= 0:
            return []
        stmt = (
            select(SearchIndexJob)
            .where(
                SearchIndexJob.status == "PROCESSING",
                SearchIndexJob.started_at.is_not(None),
            )
            .order_by(SearchIndexJob.started_at.asc(), SearchIndexJob.id.asc())
            .limit(max(limit * 5, limit))
            .with_for_update(skip_locked=True)
        )
        candidates = list(self.db.scalars(stmt).all())
        jobs: list[SearchIndexJob] = []
        for job in candidates:
            started = job.started_at
            if started is None:
                continue
            is_embed = (
                job.action == "UPSERT"
                and isinstance(job.payload_json, dict)
                and job.payload_json.get("operation") == "EMBED_SEARCH_INDEX_ITEM"
            )
            threshold = (
                embed_older_than
                if (is_embed and embed_older_than is not None)
                else older_than
            )
            if started < threshold:
                jobs.append(job)
            if len(jobs) >= limit:
                break
        recovered: list[UUID] = []
        for job in jobs:
            job.status = "PENDING"
            job.started_at = None
            job.completed_at = None
            job.error_message = None
            job.retry_count = int(job.retry_count or 0) + 1
            self.db.add(job)
            recovered.append(job.id)
        if recovered:
            self.db.flush()
        return recovered

    def list_pending_job_ids(self, *, limit: int = 50) -> list[UUID]:
        """Debug/admin helper — dispatcher must use reserve_pending_jobs instead."""
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

    def mark_job_cancelled(self, job: SearchIndexJob) -> SearchIndexJob:
        """Cancel without success semantics; does not bump retry_count."""
        job.status = "CANCELLED"
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


    def mark_job_failed_if_processing(
        self,
        job_id: UUID,
        *,
        error_message: str,
    ) -> SearchIndexJob | None:
        """Mark FAILED only when still PROCESSING (after work TX rollback)."""
        now = datetime.now(timezone.utc)
        safe = (error_message or "search index processing failed")[:500]
        stmt = (
            update(SearchIndexJob)
            .where(
                SearchIndexJob.id == job_id,
                SearchIndexJob.status == "PROCESSING",
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
        job.error_message = (error_message or "unsupported search index action")[:500]
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

    def get_item(self, item_id: UUID) -> SearchIndexItem | None:
        return self.db.get(SearchIndexItem, item_id)

    def lock_item_for_update(self, item_id: UUID) -> SearchIndexItem | None:
        # populate_existing: refresh identity-map row so post-embed stale checks
        # see commits that landed while the provider call was in flight.
        stmt = (
            select(SearchIndexItem)
            .where(SearchIndexItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.db.scalars(stmt).first()

    def get_job_by_idempotency_key(self, key: str) -> SearchIndexJob | None:
        stmt = select(SearchIndexJob).where(SearchIndexJob.idempotency_key == key)
        return self.db.scalars(stmt).first()

    def apply_item_embedding(
        self,
        item: SearchIndexItem,
        *,
        vector: list[float],
        embedding_model: str,
        embedding_version: str,
    ) -> SearchIndexItem:
        now = datetime.now(timezone.utc)
        item.embedding = vector
        item.embedding_model = embedding_model
        item.embedding_version = embedding_version
        item.indexed_at = now
        self.db.add(item)
        self.db.flush()
        return item


    def _embedding_job_outcome_for_existing(
        self,
        existing: SearchIndexJob,
        *,
        settings,
        now: datetime,
    ) -> tuple[SearchIndexJob, str]:
        """Apply current status semantics for an already-persisted Embedding job."""
        if existing.status == "PENDING":
            return existing, "already_pending"
        if existing.status == "PROCESSING":
            return existing, "already_processing"
        if existing.status == "COMPLETED":
            return existing, "already_completed"
        if existing.status == "FAILED":
            max_retries = int(settings.embedding_max_retries)
            backoff = int(settings.embedding_retry_backoff_seconds)
            retry_count = int(existing.retry_count or 0)
            if retry_count >= max_retries:
                return existing, "exhausted"
            completed_at = existing.completed_at
            if completed_at is not None:
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=timezone.utc)
                if (now - completed_at).total_seconds() < backoff:
                    return existing, "backoff"
            existing.status = "PENDING"
            existing.started_at = None
            existing.completed_at = None
            existing.error_message = None
            self.db.add(existing)
            self.db.flush()
            return existing, "requeued"
        if existing.status == "CANCELLED":
            existing.status = "PENDING"
            existing.started_at = None
            existing.completed_at = None
            existing.error_message = None
            self.db.add(existing)
            self.db.flush()
            return existing, "requeued"
        return existing, "already_pending"

    def ensure_embedding_job(
        self, item: SearchIndexItem
    ) -> tuple[SearchIndexJob | None, str]:
        """Create or requeue UPSERT Embedding job for one SearchIndexItem.

        Never publishes Celery. Never commits. Concurrent creates are race-safe via
        PostgreSQL ``INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`` so a
        UNIQUE violation cannot abort the caller's transaction (e.g. REBUILD_PERSON).

        Returns (job, outcome) where outcome is one of:
        skipped | created | requeued | already_pending | already_processing |
        already_completed | exhausted | backoff.
        """
        from app.core.config import get_settings
        from app.modules.search.embedding_policy import (
            OPERATION_EMBED_SEARCH_INDEX_ITEM,
            current_embedding_model,
            effective_embedding_version,
            embedding_idempotency_key,
            item_content_hash,
            item_needs_embedding,
            item_search_document_version,
        )

        settings = get_settings()
        if not settings.embedding_enabled:
            return None, "skipped"
        model = current_embedding_model()
        version = effective_embedding_version()
        if not item_needs_embedding(item, model=model, version=version):
            return None, "skipped"

        content_hash_value = item_content_hash(item)
        search_doc_version = item_search_document_version(item)
        key = embedding_idempotency_key(
            search_index_item_id=str(item.id),
            content_hash_value=content_hash_value,
            search_document_version=search_doc_version,
            embedding_model=model,
            embedding_version=version,
        )
        now = datetime.now(timezone.utc)
        existing = self.get_job_by_idempotency_key(key)
        if existing is not None:
            return self._embedding_job_outcome_for_existing(
                existing, settings=settings, now=now
            )

        payload = {
            "operation": OPERATION_EMBED_SEARCH_INDEX_ITEM,
            "search_index_item_id": str(item.id),
            "expected_content_hash": content_hash_value,
            "expected_search_document_version": search_doc_version,
            "embedding_model": model,
            "embedding_version": version,
        }
        # Race-safe create: do not use ORM flush (IntegrityError would poison
        # the caller's transaction). ON CONFLICT DO NOTHING keeps the TX alive.
        stmt = (
            pg_insert(SearchIndexJob)
            .values(
                person_id=item.person_id,
                object_type=item.object_type,
                object_id=item.object_id,
                action="UPSERT",
                status="PENDING",
                idempotency_key=key,
                payload_json=payload,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(SearchIndexJob.id)
        )
        inserted_id = self.db.execute(stmt).scalar_one_or_none()
        if inserted_id is not None:
            job = self.get_job(inserted_id)
            if job is None:
                # RETURNING succeeded but row not visible via ORM get — reload.
                job = self.get_job_by_idempotency_key(key)
            if job is None:
                raise RuntimeError(
                    "embedding job insert returned id but row is not readable"
                )
            return job, "created"

        existing = self.get_job_by_idempotency_key(key)
        if existing is None:
            # Conflict loser should always see the winner; one retry for visibility.
            self.db.flush()
            existing = self.get_job_by_idempotency_key(key)
        if existing is None:
            raise RuntimeError(
                "embedding job conflict occurred but existing row was not found"
            )
        return self._embedding_job_outcome_for_existing(
            existing, settings=settings, now=now
        )

    def list_items_needing_embedding(
        self,
        *,
        limit: int = 100,
        after_id: UUID | None = None,
    ) -> list[SearchIndexItem]:
        """Active PROFILE/PROJECT/DOCUMENT_CHUNK rows missing current embedding.

        Filters embedding-needed predicates in PostgreSQL (not a fixed leading
        window + Python post-filter) so completed embeddings cannot starve later
        NULL/outdated rows. Supports keyset pagination via ``after_id``.
        """
        from sqlalchemy import func, or_

        from app.core.config import get_settings
        from app.modules.search.embedding_policy import (
            current_embedding_model,
            effective_embedding_version,
            item_needs_embedding,
        )

        settings = get_settings()
        if not settings.embedding_enabled or limit <= 0:
            return []
        model = current_embedding_model()
        version = effective_embedding_version()
        predicates = [
            SearchIndexItem.is_active.is_(True),
            SearchIndexItem.object_type.in_(("PROFILE", "PROJECT", "DOCUMENT_CHUNK")),
            SearchIndexItem.search_text.is_not(None),
            func.btrim(SearchIndexItem.search_text) != "",
            or_(
                SearchIndexItem.embedding.is_(None),
                SearchIndexItem.embedding_model.is_distinct_from(model),
                SearchIndexItem.embedding_version.is_distinct_from(version),
            ),
        ]
        if after_id is not None:
            predicates.append(SearchIndexItem.id > after_id)
        stmt = (
            select(SearchIndexItem)
            .where(*predicates)
            .order_by(SearchIndexItem.id.asc())
            .limit(limit)
        )
        rows = list(self.db.scalars(stmt).all())
        # Defensive Python check (blank / type) — DB already filtered needs.
        return [
            item
            for item in rows
            if item_needs_embedding(item, model=model, version=version)
        ]



def lock_person_profile_for_update(db: Session, person_id: UUID) -> PersonProfile | None:
    """Lock PersonProfile row for a consistent Confirmed snapshot during rebuild."""
    stmt = (
        select(PersonProfile)
        .where(PersonProfile.person_id == person_id)
        .with_for_update()
    )
    return db.scalars(stmt).first()
