"""Shared Confirmed Profile mutation finalization."""

from __future__ import annotations

from uuid import UUID

from app.db.models.person import Person, PersonProfile
from app.modules.people.repository import PeopleRepository
from app.modules.people.snapshot import build_confirmed_profile_snapshot


def finalize_confirmed_profile_change(
    repo: PeopleRepository,
    *,
    person: Person,
    profile: PersonProfile,
    actor_user_id: UUID,
    action_type: str,
    target_type: str,
    target_id: UUID,
    before: dict | None = None,
    after: dict | None = None,
    metadata: dict | None = None,
) -> int:
    """Bump profile version and write revision / audit / search index job.

    Caller must already have applied business-data changes. This helper flushes
    so the snapshot reflects the post-change state, then records side effects.
    Does **not** commit — the caller owns the transaction boundary.
    """
    repo.db.flush()
    version = repo.bump_profile_version(profile)
    repo.touch_person(person)
    snapshot = build_confirmed_profile_snapshot(repo.db, person.id)
    repo.add_revision(
        person_id=person.id,
        revision_no=version,
        snapshot=snapshot,
        created_by=actor_user_id,
    )
    meta = {"person_id": str(person.id), **(metadata or {})}
    repo.add_audit(
        action_type=action_type,
        actor_user_id=actor_user_id,
        person_id=person.id,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
        metadata=meta,
    )
    repo.enqueue_rebuild_person(person.id, version)
    return version
