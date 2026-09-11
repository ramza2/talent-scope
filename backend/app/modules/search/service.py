"""Search index job processing — Confirmed DB → PROFILE/PROJECT SearchIndexItem."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.person import Person
from app.db.models.search import SearchIndexJob
from app.db.session import SessionLocal
from app.modules.people.snapshot import build_confirmed_profile_snapshot
from app.modules.search.document_builder import build_search_documents_for_person
from app.modules.search.errors import SearchIndexProcessingError, safe_search_job_error
from app.modules.search.repository import (
    TERMINAL_JOB_STATUSES,
    SearchRepository,
    lock_person_profile_for_update,
)
from app.modules.search.schemas import (
    OBJECT_TYPE_PROFILE,
    OBJECT_TYPE_PROJECT,
)

logger = logging.getLogger(__name__)

SUPPORTED_ACTIONS = frozenset({"REBUILD_PERSON"})

# External APIs are not used in REBUILD_PERSON; 5 minutes is a conservative stuck timeout.
STALE_PROCESSING_TIMEOUT = timedelta(minutes=5)
DEFAULT_RESERVE_LIMIT = 50
DEFAULT_RECOVER_LIMIT = 100


@dataclass(frozen=True)
class ProcessJobResult:
    job_id: UUID
    status: str
    claimed: bool
    profile_document_count: int = 0
    project_document_count: int = 0
    upserted_count: int = 0
    deactivated_count: int = 0


class SearchIndexService:
    """Process SearchIndexJob rows against live Confirmed Profile state.

    Lifecycle:
      Dispatcher: PENDING → PROCESSING (reserve + commit) → Celery publish
      Worker: lock PROCESSING → rebuild SearchIndexItem → COMPLETED (work TX)
      Direct call: PENDING → PROCESSING commit, then same worker path
      Failure: work TX rollback; short TX marks PROCESSING → FAILED (sanitized error)
      Stale: PROCESSING with old started_at → PENDING (retry_count += 1)
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SearchRepository(db)

    def reserve_pending_jobs(self, limit: int = DEFAULT_RESERVE_LIMIT) -> list[SearchIndexJob]:
        """Atomic PENDING→PROCESSING reserve and COMMIT (dispatch visibility)."""
        jobs = self.repo.reserve_pending_jobs(limit=max(0, min(int(limit), 500)))
        self.db.commit()
        for job in jobs:
            self.db.refresh(job)
        return jobs

    def release_reserved_job(self, job_id: UUID) -> bool:
        """Publish-failure recovery: PROCESSING→PENDING when still reserved."""
        released = self.repo.release_reserved_job(job_id)
        self.db.commit()
        return released

    def recover_stale_processing_jobs(
        self,
        *,
        older_than: datetime | None = None,
        limit: int = DEFAULT_RECOVER_LIMIT,
    ) -> list[UUID]:
        threshold = older_than or (datetime.now(timezone.utc) - STALE_PROCESSING_TIMEOUT)
        recovered = self.repo.recover_stale_processing_jobs(
            older_than=threshold,
            limit=max(0, min(int(limit), 500)),
        )
        self.db.commit()
        return recovered

    def process_job(self, job_id: UUID) -> ProcessJobResult:
        existing = self.repo.get_job(job_id)
        if existing is None:
            raise SearchIndexProcessingError("search index job not found")

        if existing.status in TERMINAL_JOB_STATUSES:
            return ProcessJobResult(
                job_id=job_id,
                status=existing.status,
                claimed=False,
            )

        # Backward-compatible direct/manual call: PENDING → PROCESSING then work path.
        if existing.status == "PENDING":
            claimed = self.repo.claim_pending_job(job_id)
            if claimed is None:
                self.db.rollback()
                current = self.repo.get_job(job_id)
                if current is None:
                    raise SearchIndexProcessingError("search index job not found")
                if current.status in TERMINAL_JOB_STATUSES or current.status != "PROCESSING":
                    return ProcessJobResult(
                        job_id=job_id,
                        status=current.status,
                        claimed=False,
                    )
            else:
                self.db.commit()

        try:
            return self._process_processing_job(job_id)
        except SearchIndexProcessingError:
            raise
        except Exception as exc:
            self.db.rollback()
            safe = safe_search_job_error(exc)
            self._mark_failed_after_rollback(job_id, safe, exc_class=type(exc).__name__)
            raise SearchIndexProcessingError(safe) from None

    def _process_processing_job(self, job_id: UUID) -> ProcessJobResult:
        locked = self.repo.lock_processing_job(job_id)
        if locked is None:
            self.db.rollback()
            current = self.repo.get_job(job_id)
            if current is None:
                raise SearchIndexProcessingError("search index job not found")
            return ProcessJobResult(
                job_id=job_id,
                status=current.status,
                claimed=False,
            )

        if locked.action not in SUPPORTED_ACTIONS:
            self.repo.mark_unsupported_action_failed(
                locked,
                error_message="unsupported search index action",
            )
            self.db.commit()
            return ProcessJobResult(job_id=job_id, status="FAILED", claimed=True)

        if locked.person_id is None:
            self.repo.mark_unsupported_action_failed(
                locked,
                error_message="rebuild person requires person_id",
            )
            self.db.commit()
            return ProcessJobResult(job_id=job_id, status="FAILED", claimed=True)

        try:
            result = self._rebuild_person(locked)
            self.db.commit()
            return result
        except SearchIndexProcessingError as exc:
            self.db.rollback()
            safe = safe_search_job_error(exc)
            self._mark_failed_after_rollback(job_id, safe, exc_class=type(exc).__name__)
            raise SearchIndexProcessingError(safe) from None
        except Exception as exc:
            self.db.rollback()
            safe = safe_search_job_error(exc)
            logger.warning(
                "search_index work failed job_id=%s person_id=%s exc_class=%s",
                job_id,
                locked.person_id,
                type(exc).__name__,
            )
            self._mark_failed_after_rollback(job_id, safe, exc_class=type(exc).__name__)
            raise SearchIndexProcessingError(safe) from None

    def _rebuild_person(self, job: SearchIndexJob) -> ProcessJobResult:
        person_id = job.person_id
        assert person_id is not None

        lock_person_profile_for_update(self.db, person_id)

        person = self.db.get(Person, person_id)
        if person is None:
            raise SearchIndexProcessingError("person not found for search index rebuild")

        if person.status == "DELETED" or person.deleted_at is not None:
            deactivated = self.repo.deactivate_all_profile_project_for_person(person_id)
            self.repo.mark_job_completed(job)
            logger.info(
                "search_index rebuild deleted person job_id=%s person_id=%s deactivated=%s",
                job.id,
                person_id,
                deactivated,
            )
            return ProcessJobResult(
                job_id=job.id,
                status="COMPLETED",
                claimed=True,
                deactivated_count=deactivated,
            )

        snapshot = build_confirmed_profile_snapshot(self.db, person_id)
        try:
            documents = build_search_documents_for_person(
                person_id=person_id,
                person_status=person.status,
                snapshot=snapshot,
            )
        except Exception as exc:
            raise SearchIndexProcessingError("search document build failed") from exc

        desired_keys: set[tuple[str, UUID]] = set()
        upserted = 0
        profile_count = 0
        project_count = 0
        for doc in documents:
            self.repo.upsert_search_document(doc)
            upserted += 1
            desired_keys.add((doc.object_type, doc.object_id))
            if doc.object_type == OBJECT_TYPE_PROFILE:
                profile_count += 1
            elif doc.object_type == OBJECT_TYPE_PROJECT:
                project_count += 1

        active_items = self.repo.list_active_items_for_person(person_id)
        stale = [
            item
            for item in active_items
            if (item.object_type, item.object_id) not in desired_keys
        ]
        deactivated = self.repo.deactivate_items(stale)

        profile = snapshot.get("profile") or {}
        self.repo.mark_job_completed(job)

        logger.info(
            "search_index rebuild completed job_id=%s person_id=%s "
            "profile_version=%s profile_docs=%s project_docs=%s "
            "upserted=%s deactivated=%s",
            job.id,
            person_id,
            profile.get("profile_version"),
            profile_count,
            project_count,
            upserted,
            deactivated,
        )
        return ProcessJobResult(
            job_id=job.id,
            status="COMPLETED",
            claimed=True,
            profile_document_count=profile_count,
            project_document_count=project_count,
            upserted_count=upserted,
            deactivated_count=deactivated,
        )

    def _mark_failed_after_rollback(
        self,
        job_id: UUID,
        safe_message: str,
        *,
        exc_class: str,
    ) -> None:
        fail_db = SessionLocal()
        try:
            repo = SearchRepository(fail_db)
            marked = repo.mark_job_failed_if_processing(
                job_id, error_message=safe_message
            )
            fail_db.commit()
            if marked is not None:
                logger.warning(
                    "search_index job failed job_id=%s status=%s retry_count=%s "
                    "exc_class=%s error=%s",
                    job_id,
                    marked.status,
                    marked.retry_count,
                    exc_class,
                    safe_message,
                )
        except Exception:
            fail_db.rollback()
            logger.exception(
                "search_index failed to record FAILED status job_id=%s", job_id
            )
        finally:
            fail_db.close()


def process_search_index_job_id(job_id: UUID | str) -> ProcessJobResult:
    """Celery entry helper: own SessionLocal, process one job."""
    db = SessionLocal()
    try:
        return SearchIndexService(db).process_job(UUID(str(job_id)))
    finally:
        db.close()
