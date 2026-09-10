"""Search index job processing — Confirmed DB → PROFILE/PROJECT SearchIndexItem."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models.person import Person
from app.db.models.search import SearchIndexJob
from app.db.session import SessionLocal
from app.modules.people.snapshot import build_confirmed_profile_snapshot
from app.modules.search.document_builder import build_search_documents_for_person
from app.modules.search.repository import SearchRepository, lock_person_profile_for_update
from app.modules.search.schemas import (
    ERROR_MESSAGE_MAX_CHARS,
    OBJECT_TYPE_PROFILE,
    OBJECT_TYPE_PROJECT,
)

logger = logging.getLogger(__name__)

SUPPORTED_ACTIONS = frozenset({"REBUILD_PERSON"})


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

    Transaction policy (single TX, no intermediate commit):
      claim → lock profile → snapshot → build → upsert/deactivate → COMPLETED → COMMIT

    Hard-kill before COMMIT rolls the claim back to PENDING (dispatcher recoverable).
    Handled exceptions roll back, then mark FAILED in a separate short transaction.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SearchRepository(db)

    def process_job(self, job_id: UUID) -> ProcessJobResult:
        try:
            return self._process_job_in_transaction(job_id)
        except Exception as exc:
            self.db.rollback()
            self._mark_failed_after_rollback(job_id, exc)
            raise

    def _process_job_in_transaction(self, job_id: UUID) -> ProcessJobResult:
        existing = self.repo.get_job(job_id)
        if existing is None:
            raise ValueError(f"SearchIndexJob not found: {job_id}")

        claimed = self.repo.claim_pending_job(job_id)
        if claimed is None:
            current = self.repo.get_job(job_id)
            assert current is not None
            return ProcessJobResult(
                job_id=job_id,
                status=current.status,
                claimed=False,
            )

        if claimed.action not in SUPPORTED_ACTIONS:
            self.repo.mark_unsupported_action_failed(
                claimed,
                error_message=(
                    f"unsupported SearchIndexJob.action={claimed.action!r}; "
                    "only REBUILD_PERSON is implemented"
                ),
            )
            self.db.commit()
            return ProcessJobResult(job_id=job_id, status="FAILED", claimed=True)

        if claimed.person_id is None:
            self.repo.mark_unsupported_action_failed(
                claimed,
                error_message="REBUILD_PERSON requires person_id",
            )
            self.db.commit()
            return ProcessJobResult(job_id=job_id, status="FAILED", claimed=True)

        result = self._rebuild_person(claimed)
        self.db.commit()
        return result

    def _rebuild_person(self, job: SearchIndexJob) -> ProcessJobResult:
        person_id = job.person_id
        assert person_id is not None

        # Lock PersonProfile only (no Project/Career FOR UPDATE).
        lock_person_profile_for_update(self.db, person_id)

        person = self.db.get(Person, person_id)
        if person is None:
            self.repo.mark_unsupported_action_failed(
                job,
                error_message="person not found for REBUILD_PERSON",
            )
            return ProcessJobResult(job_id=job.id, status="FAILED", claimed=True)

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

        # Always rebuild from live Confirmed state (ignore stale payload version).
        snapshot = build_confirmed_profile_snapshot(self.db, person_id)
        documents = build_search_documents_for_person(
            person_id=person_id,
            person_status=person.status,
            snapshot=snapshot,
        )

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

    def _mark_failed_after_rollback(self, job_id: UUID, exc: BaseException) -> None:
        message = _safe_error_message(exc)
        fail_db = SessionLocal()
        try:
            repo = SearchRepository(fail_db)
            marked = repo.mark_job_failed_if_pending(job_id, error_message=message)
            fail_db.commit()
            if marked is not None:
                logger.warning(
                    "search_index job failed job_id=%s retry_count=%s error=%s",
                    job_id,
                    marked.retry_count,
                    message,
                )
        except Exception:
            fail_db.rollback()
            logger.exception(
                "search_index failed to record FAILED status job_id=%s", job_id
            )
        finally:
            fail_db.close()


def _safe_error_message(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    if len(text) > ERROR_MESSAGE_MAX_CHARS:
        text = text[:ERROR_MESSAGE_MAX_CHARS].rstrip()
    return text or "unknown error"


def process_search_index_job_id(job_id: UUID | str) -> ProcessJobResult:
    """Celery entry helper: own SessionLocal, process one job."""
    db = SessionLocal()
    try:
        return SearchIndexService(db).process_job(UUID(str(job_id)))
    finally:
        db.close()
