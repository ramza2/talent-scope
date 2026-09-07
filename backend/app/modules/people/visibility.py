"""Person visibility helpers shared across modules."""

from __future__ import annotations

from app.core.exceptions import NotFoundError
from app.db.models.person import Person


def ensure_person_readable(person: Person, *, is_admin: bool) -> None:
    """Hide DELETED persons from non-admins (404, not 403).

    Matches People Core GET /people/{id} policy from PR #5.
    """
    if person.status == "DELETED" and not is_admin:
        raise NotFoundError("인력을 찾을 수 없습니다.")
