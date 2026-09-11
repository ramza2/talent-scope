"""DocumentGroup → DocumentChunk → DOCUMENT_CHUNK SearchIndexItem sync."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.document import Document, DocumentChunk, DocumentGroup, DocumentPage
from app.db.models.person import Person
from app.db.models.search import SearchIndexItem, SearchIndexJob
from app.modules.document_processing.chunker import (
    DOCUMENT_CHUNKER_VERSION,
    ChunkSpec,
    build_chunk_specs_for_pages,
    pages_source_fingerprint,
)
from app.modules.search.document_chunk_policy import (
    DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
    OBJECT_TYPE_DOCUMENT_CHUNK,
    OPERATION_SYNC_DOCUMENT_CHUNKS,
    SOURCE_WEIGHT_DOCUMENT_CHUNK,
    build_document_chunk_row_metadata,
    build_document_chunk_search_metadata,
    document_chunk_sync_fingerprint,
    document_chunk_sync_idempotency_key,
)

logger = logging.getLogger(__name__)


class DocumentChunkSyncService:
    """Materialize DocumentChunks and sync DOCUMENT_CHUNK SearchIndexItems.

    Source-state serialization (lock order):
      SearchIndexJob (worker) → Person → DocumentGroup

    ``ensure_sync_job`` acquires DocumentGroup FOR UPDATE in the caller TX
    (no commit) so READY/delete/restore/status mutations share the same
    serialization point as the sync worker.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Shared locks
    # ------------------------------------------------------------------

    def lock_person_for_update(self, person_id: UUID) -> Person | None:
        stmt = (
            select(Person)
            .where(Person.id == person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.db.scalars(stmt).first()

    def lock_document_group_for_update(
        self, document_group_id: UUID
    ) -> DocumentGroup | None:
        stmt = (
            select(DocumentGroup)
            .where(DocumentGroup.id == document_group_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self.db.scalars(stmt).first()

    def _person_after_group_lock(self, person_id: UUID) -> Person | None:
        """Load Person under Group lock without wiping uncommitted local mutations.

        PeopleService.update_status() holds dirty Person.status in the same TX
        before ensure_sync_job. ``populate_existing`` / blind refresh would reload
        the last committed row and undo DELETED/ACTIVE transitions.
        """
        from sqlalchemy import inspect as sa_inspect

        person = self.db.get(Person, person_id)
        if person is None:
            return self.db.scalars(
                select(Person).where(Person.id == person_id)
            ).first()
        insp = sa_inspect(person)
        if insp.modified or insp.pending:
            return person
        # Not mutated in this TX — refresh committed state after Group lock.
        self.db.refresh(person)
        return person

    def _after_source_locks(
        self,
        *,
        person: Person,
        group: DocumentGroup,
        effective: Document | None,
    ) -> None:
        """Extension point after Person+Group locks (tests may monkeypatch)."""
        return None

    # ------------------------------------------------------------------
    # Job ensure (caller TX; no commit / no Celery)
    # ------------------------------------------------------------------

    def ensure_sync_job(
        self,
        document_group_id: UUID,
    ) -> tuple[SearchIndexJob | None, str]:
        # Group FOR UPDATE held until caller commits — serializes with sync worker.
        group = self.lock_document_group_for_update(document_group_id)
        if group is None:
            return None, "skipped"

        # Same-TX Document READY/delete mutations must be visible to effective SELECT.
        self.db.flush()

        person = self._person_after_group_lock(group.person_id)
        person_searchable = self._person_group_searchable(person, group)

        effective = self.get_effective_ready_document(document_group_id)
        pages = self.list_pages(effective.id) if effective is not None else []
        source_fp = pages_source_fingerprint(pages)
        updated_at = ""
        if effective is not None and effective.updated_at is not None:
            updated_at = effective.updated_at.isoformat()

        fingerprint = document_chunk_sync_fingerprint(
            document_group_id=document_group_id,
            effective_document_id=effective.id if effective else None,
            effective_document_updated_at=updated_at,
            source_fingerprint=source_fp,
            chunker_version=DOCUMENT_CHUNKER_VERSION,
            search_document_version=DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
            person_searchable=person_searchable,
        )
        key = document_chunk_sync_idempotency_key(
            document_group_id=document_group_id,
            fingerprint=fingerprint,
        )

        existing = self.db.scalars(
            select(SearchIndexJob).where(SearchIndexJob.idempotency_key == key)
        ).first()
        if existing is not None:
            if existing.status in {"PENDING", "PROCESSING"}:
                return existing, "already_pending"
            if existing.status == "COMPLETED":
                # Same source fingerprint may be COMPLETED while SearchIndexItems
                # still reflect a newer version that was later removed/failed.
                # Requeue when live active items do not match expected effective doc.
                if self._active_chunk_items_match_effective(
                    document_group_id=document_group_id,
                    effective=effective,
                    person_searchable=person_searchable,
                ):
                    return existing, "already_completed"
                existing.status = "PENDING"
                existing.started_at = None
                existing.completed_at = None
                existing.error_message = None
                self.db.add(existing)
                self.db.flush()
                return existing, "requeued"
            if existing.status in {"FAILED", "CANCELLED"}:
                existing.status = "PENDING"
                existing.started_at = None
                existing.completed_at = None
                existing.error_message = None
                self.db.add(existing)
                self.db.flush()
                return existing, "requeued"
            return existing, "already_pending"

        payload = {
            "operation": OPERATION_SYNC_DOCUMENT_CHUNKS,
            "document_group_id": str(document_group_id),
            "fingerprint": fingerprint,
            "chunker_version": DOCUMENT_CHUNKER_VERSION,
            "search_document_version": DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
        }
        stmt = (
            pg_insert(SearchIndexJob)
            .values(
                person_id=group.person_id,
                object_type=OBJECT_TYPE_DOCUMENT_CHUNK,
                object_id=document_group_id,
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
            job = self.db.get(SearchIndexJob, inserted_id)
            return job, "created"

        existing = self.db.scalars(
            select(SearchIndexJob).where(SearchIndexJob.idempotency_key == key)
        ).first()
        if existing is None:
            raise RuntimeError("document chunk sync job conflict but row missing")
        return existing, "already_pending"

    # ------------------------------------------------------------------
    # Sync execution (worker TX; no embedding provider calls)
    # ------------------------------------------------------------------

    def sync_document_group(self, document_group_id: UUID) -> dict[str, int]:
        from app.modules.search.repository import SearchRepository

        # A. Plain probe for person_id only (do not trust this for source state).
        probe = self.db.get(DocumentGroup, document_group_id)
        if probe is None:
            return {"chunks": 0, "upserted": 0, "deactivated": 0, "embedding_jobs": 0}
        person_id = probe.person_id

        # B/C. Lock order: Person → DocumentGroup (matches PeopleService status TX).
        person = self.lock_person_for_update(person_id)
        group = self.lock_document_group_for_update(document_group_id)
        if group is None:
            return {"chunks": 0, "upserted": 0, "deactivated": 0, "embedding_jobs": 0}
        if person is None:
            person = self._person_after_group_lock(group.person_id)

        # D. Recompute searchable / effective from post-lock populated rows.
        if not self._person_group_searchable(person, group):
            deactivated = self.deactivate_group_search_items(document_group_id)
            logger.info(
                "document_chunk sync deactivated non-searchable "
                "group_id=%s person_id=%s deactivated=%s",
                document_group_id,
                group.person_id,
                deactivated,
            )
            return {
                "chunks": 0,
                "upserted": 0,
                "deactivated": deactivated,
                "embedding_jobs": 0,
            }

        effective = self.get_effective_ready_document(document_group_id)
        self._after_source_locks(person=person, group=group, effective=effective)

        if effective is None:
            deactivated = self.deactivate_group_search_items(document_group_id)
            logger.info(
                "document_chunk sync no ready document group_id=%s deactivated=%s",
                document_group_id,
                deactivated,
            )
            return {
                "chunks": 0,
                "upserted": 0,
                "deactivated": deactivated,
                "embedding_jobs": 0,
            }

        # Plain SELECTs under Group lock — Document mutation serializes via ensure.
        pages = self.list_pages(effective.id)
        specs, source_fp = build_chunk_specs_for_pages(pages)

        deactivated_other = self.deactivate_non_effective_version_items(
            document_group_id=document_group_id,
            effective_document_id=effective.id,
        )

        # Deactivate items for chunk indexes that will disappear before deleting rows.
        existing_chunks = {
            int(c.chunk_index): c
            for c in self.db.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == effective.id)
            ).all()
        }
        keep_indexes = {s.chunk_index for s in specs}
        deactivated_trailing = 0
        for idx, row in existing_chunks.items():
            if idx not in keep_indexes:
                deactivated_trailing += self._deactivate_items_for_chunk_id(row.id)

        chunk_rows = self.sync_chunk_rows(
            document_id=effective.id,
            specs=specs,
            source_fingerprint=source_fp,
            existing=existing_chunks,
            keep_indexes=keep_indexes,
        )

        search_repo = SearchRepository(self.db)
        upserted = 0
        embedding_jobs = 0
        for row in chunk_rows:
            meta = row.metadata_ if isinstance(row.metadata_, dict) else {}
            search_meta = build_document_chunk_search_metadata(
                chunk_id=row.id,
                document_id=effective.id,
                document_group_id=document_group_id,
                document_type_code=group.document_type_code,
                document_version_no=effective.version_no,
                chunk_index=row.chunk_index,
                page_from=int(row.page_from or 0),
                page_to=int(row.page_to or 0),
                char_start=int(meta.get("char_start") or 0),
                char_end=int(meta.get("char_end") or 0),
                extraction_method=meta.get("extraction_method"),
                chunker_version=str(
                    meta.get("chunker_version") or DOCUMENT_CHUNKER_VERSION
                ),
                chunk_hash_value=str(row.chunk_hash or ""),
                search_text=row.chunk_text,
                document_date=(
                    effective.document_date.isoformat()
                    if getattr(effective, "document_date", None) is not None
                    else None
                ),
                document_title=group.title,
                original_filename=effective.original_filename,
            )
            item = self.upsert_chunk_search_item(
                person_id=group.person_id,
                chunk=row,
                metadata=search_meta,
            )
            upserted += 1
            _job, outcome = search_repo.ensure_embedding_job(item)
            if outcome in {"created", "requeued"}:
                embedding_jobs += 1

        self.db.flush()
        logger.info(
            "document_chunk sync completed group_id=%s document_id=%s "
            "page_count=%s chunk_count=%s upserted=%s deactivated=%s "
            "embedding_jobs=%s chunker_version=%s",
            document_group_id,
            effective.id,
            len(pages),
            len(chunk_rows),
            upserted,
            deactivated_other + deactivated_trailing,
            embedding_jobs,
            DOCUMENT_CHUNKER_VERSION,
        )
        return {
            "chunks": len(chunk_rows),
            "upserted": upserted,
            "deactivated": deactivated_other + deactivated_trailing,
            "embedding_jobs": embedding_jobs,
        }

    # ------------------------------------------------------------------
    # Queries / mutations
    # ------------------------------------------------------------------

    @staticmethod
    def _person_group_searchable(
        person: Person | None, group: DocumentGroup
    ) -> bool:
        return bool(
            person is not None
            and person.deleted_at is None
            and person.status != "DELETED"
            and group.deleted_at is None
        )

    def get_effective_ready_document(
        self, document_group_id: UUID
    ) -> Document | None:
        stmt = (
            select(Document)
            .where(
                Document.document_group_id == document_group_id,
                Document.deleted_at.is_(None),
                Document.processing_status == "READY",
            )
            .order_by(Document.version_no.desc(), Document.id.asc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return self.db.scalars(stmt).first()

    def list_pages(self, document_id: UUID) -> list[DocumentPage]:
        stmt = (
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_no.asc(), DocumentPage.id.asc())
            .execution_options(populate_existing=True)
        )
        return list(self.db.scalars(stmt).all())

    def sync_chunk_rows(
        self,
        *,
        document_id: UUID,
        specs: list[ChunkSpec],
        source_fingerprint: str,
        existing: dict[int, DocumentChunk],
        keep_indexes: set[int],
    ) -> list[DocumentChunk]:
        kept: list[DocumentChunk] = []
        for spec in specs:
            row_meta = build_document_chunk_row_metadata(
                chunker_version=DOCUMENT_CHUNKER_VERSION,
                page_no=spec.page_no,
                char_start=spec.char_start,
                char_end=spec.char_end,
                extraction_method=spec.extraction_method,
                source_fingerprint=source_fingerprint,
            )
            row = existing.get(spec.chunk_index)
            if row is None:
                row = DocumentChunk(
                    id=uuid4(),
                    document_id=document_id,
                    chunk_index=spec.chunk_index,
                    page_from=spec.page_from,
                    page_to=spec.page_to,
                    chunk_text=spec.chunk_text,
                    token_count=None,
                    chunk_hash=spec.chunk_hash,
                    metadata_=row_meta,
                )
                self.db.add(row)
            else:
                row.page_from = spec.page_from
                row.page_to = spec.page_to
                row.chunk_text = spec.chunk_text
                row.token_count = None
                row.chunk_hash = spec.chunk_hash
                row.metadata_ = row_meta
                self.db.add(row)
            kept.append(row)
        self.db.flush()

        for idx, row in list(existing.items()):
            if idx not in keep_indexes:
                self.db.delete(row)
        self.db.flush()
        return kept

    def upsert_chunk_search_item(
        self,
        *,
        person_id: UUID,
        chunk: DocumentChunk,
        metadata: dict,
    ) -> SearchIndexItem:
        now = datetime.now(timezone.utc)
        stmt = (
            select(SearchIndexItem)
            .where(
                SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                SearchIndexItem.object_id == chunk.id,
                SearchIndexItem.is_active.is_(True),
            )
            .order_by(SearchIndexItem.created_at.asc(), SearchIndexItem.id.asc())
        )
        rows = list(self.db.scalars(stmt).all())
        canonical = rows[0] if rows else None
        for extra in rows[1:]:
            extra.is_active = False
            self.db.add(extra)

        version = str(metadata.get("search_document_version") or "")
        if canonical is None:
            inactive = self.db.scalars(
                select(SearchIndexItem)
                .where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id == chunk.id,
                )
                .order_by(SearchIndexItem.created_at.asc())
            ).first()
            if inactive is not None:
                canonical = inactive

        if canonical is None:
            item = SearchIndexItem(
                id=uuid4(),
                person_id=person_id,
                object_type=OBJECT_TYPE_DOCUMENT_CHUNK,
                object_id=chunk.id,
                search_text=chunk.chunk_text,
                embedding=None,
                source_weight=SOURCE_WEIGHT_DOCUMENT_CHUNK,
                metadata_json=metadata,
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
        same_text = (canonical.search_text or "") == chunk.chunk_text
        same_version = prev_version == version and version != ""

        canonical.person_id = person_id
        canonical.search_text = chunk.chunk_text
        canonical.source_weight = SOURCE_WEIGHT_DOCUMENT_CHUNK
        canonical.metadata_json = metadata
        canonical.is_active = True
        canonical.indexed_at = now
        if not (same_text and same_version):
            canonical.embedding = None
            canonical.embedding_model = None
            canonical.embedding_version = None
        self.db.add(canonical)
        self.db.flush()
        return canonical

    def _deactivate_items_for_chunk_id(self, chunk_id: UUID) -> int:
        items = list(
            self.db.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id == chunk_id,
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        for item in items:
            item.is_active = False
            self.db.add(item)
        if items:
            self.db.flush()
        return len(items)

    def deactivate_group_search_items(self, document_group_id: UUID) -> int:
        doc_ids = list(
            self.db.scalars(
                select(Document.id).where(
                    Document.document_group_id == document_group_id
                )
            ).all()
        )
        if not doc_ids:
            return 0
        chunk_ids = list(
            self.db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id.in_(doc_ids))
            ).all()
        )
        if not chunk_ids:
            return 0
        items = list(
            self.db.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id.in_(chunk_ids),
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        for item in items:
            item.is_active = False
            self.db.add(item)
        if items:
            self.db.flush()
        return len(items)

    def deactivate_non_effective_version_items(
        self,
        *,
        document_group_id: UUID,
        effective_document_id: UUID,
    ) -> int:
        other_doc_ids = list(
            self.db.scalars(
                select(Document.id).where(
                    Document.document_group_id == document_group_id,
                    Document.id != effective_document_id,
                )
            ).all()
        )
        if not other_doc_ids:
            return 0
        chunk_ids = list(
            self.db.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_id.in_(other_doc_ids)
                )
            ).all()
        )
        if not chunk_ids:
            return 0
        items = list(
            self.db.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id.in_(chunk_ids),
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        for item in items:
            item.is_active = False
            self.db.add(item)
        if items:
            self.db.flush()
        return len(items)

    def list_groups_needing_chunk_sync(self, *, limit: int = 100) -> list[UUID]:
        """Groups needing a current COMPLETED sync (includes zero-chunk markers).

        Uses keyset pagination over all candidate groups so already-synced
        groups ahead in the table cannot starve later unsynced groups.
        """
        if limit <= 0:
            return []
        needed: list[UUID] = []
        last_id: UUID | None = None
        batch_size = max(limit * 5, 100)
        while len(needed) < limit:
            stmt = (
                select(DocumentGroup.id)
                .join(Person, Person.id == DocumentGroup.person_id)
                .where(
                    DocumentGroup.deleted_at.is_(None),
                    Person.deleted_at.is_(None),
                    Person.status != "DELETED",
                )
                .order_by(DocumentGroup.id.asc())
                .limit(batch_size)
            )
            if last_id is not None:
                stmt = stmt.where(DocumentGroup.id > last_id)
            candidates = list(self.db.scalars(stmt).all())
            if not candidates:
                break
            for group_id in candidates:
                last_id = group_id
                group = self.db.get(DocumentGroup, group_id)
                if group is None:
                    continue
                person = self.db.get(Person, group.person_id)
                person_searchable = self._person_group_searchable(person, group)
                effective = self.get_effective_ready_document(group_id)
                pages = self.list_pages(effective.id) if effective else []
                source_fp = pages_source_fingerprint(pages)
                updated_at = ""
                if effective is not None and effective.updated_at is not None:
                    updated_at = effective.updated_at.isoformat()
                fingerprint = document_chunk_sync_fingerprint(
                    document_group_id=group_id,
                    effective_document_id=effective.id if effective else None,
                    effective_document_updated_at=updated_at,
                    source_fingerprint=source_fp,
                    chunker_version=DOCUMENT_CHUNKER_VERSION,
                    search_document_version=DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
                    person_searchable=person_searchable,
                )
                key = document_chunk_sync_idempotency_key(
                    document_group_id=group_id,
                    fingerprint=fingerprint,
                )
                existing = self.db.scalars(
                    select(SearchIndexJob).where(SearchIndexJob.idempotency_key == key)
                ).first()
                if existing is not None and existing.status in {
                    "PENDING",
                    "PROCESSING",
                }:
                    continue
                if existing is not None and existing.status == "COMPLETED":
                    if self._active_chunk_items_match_effective(
                        document_group_id=group_id,
                        effective=effective,
                        person_searchable=person_searchable,
                    ):
                        continue
                    # Stale COMPLETED marker — needs requeue via ensure.
                if effective is None and not self._group_has_any_chunk_items(group_id):
                    # Cold empty group: nothing to materialize or deactivate.
                    continue
                needed.append(group_id)
                if len(needed) >= limit:
                    break
        return needed

    def _active_chunk_items_match_effective(
        self,
        *,
        document_group_id: UUID,
        effective: Document | None,
        person_searchable: bool,
    ) -> bool:
        """Return True when active DOCUMENT_CHUNK items match expected live state."""
        active_ids = self._active_chunk_object_ids(document_group_id)
        if not person_searchable or effective is None:
            return len(active_ids) == 0
        expected_chunk_ids = set(
            self.db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id == effective.id)
            ).all()
        )
        # Zero-text READY docs: no chunks and no active items.
        if not expected_chunk_ids:
            return len(active_ids) == 0
        return active_ids == expected_chunk_ids

    def _active_chunk_object_ids(self, document_group_id: UUID) -> set[UUID]:
        doc_ids = list(
            self.db.scalars(
                select(Document.id).where(
                    Document.document_group_id == document_group_id
                )
            ).all()
        )
        if not doc_ids:
            return set()
        chunk_ids = list(
            self.db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id.in_(doc_ids))
            ).all()
        )
        if not chunk_ids:
            return set()
        return set(
            self.db.scalars(
                select(SearchIndexItem.object_id).where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id.in_(chunk_ids),
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )

    def _group_has_any_chunk_items(self, document_group_id: UUID) -> bool:
        doc_ids = list(
            self.db.scalars(
                select(Document.id).where(
                    Document.document_group_id == document_group_id
                )
            ).all()
        )
        if not doc_ids:
            return False
        chunk_ids = list(
            self.db.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id.in_(doc_ids))
            ).all()
        )
        if not chunk_ids:
            return False
        return (
            self.db.scalars(
                select(SearchIndexItem.id)
                .where(
                    SearchIndexItem.object_type == OBJECT_TYPE_DOCUMENT_CHUNK,
                    SearchIndexItem.object_id.in_(chunk_ids),
                )
                .limit(1)
            ).first()
            is not None
        )
