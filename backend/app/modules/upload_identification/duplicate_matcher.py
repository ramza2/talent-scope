"""Deterministic duplicate candidate matching (no LLM scoring)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ai.schemas.identity import IdentityExtraction
from app.db.models.person import Person, PersonProfile

MAX_DUPLICATE_CANDIDATES = 10

# Additive score weights — capped at 1.0. Deterministic and unit-tested.
SCORE_EMAIL_EXACT = 0.55
SCORE_PHONE_EXACT = 0.50
SCORE_NAME_COMPANY_EXACT = 0.35
SCORE_NAME_EXACT = 0.20
SCORE_COMPANY_EXACT = 0.10


@dataclass(frozen=True)
class DuplicateCandidate:
    person_id: UUID
    name: str
    company: str | None
    match_reasons: tuple[str, ...]
    score: float

    def to_dict(self) -> dict:
        return {
            "person_id": str(self.person_id),
            "name": self.name,
            "company": self.company,
            "match_reasons": list(self.match_reasons),
            "score": round(self.score, 4),
        }


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def normalize_phone_digits(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    return digits or None


def normalize_name(value: str | None) -> str | None:
    if not value:
        return None
    collapsed = re.sub(r"\s+", " ", value.strip()).casefold()
    return collapsed or None


def normalize_company(value: str | None) -> str | None:
    return normalize_name(value)


def score_candidate(
    *,
    identity: IdentityExtraction,
    profile_name: str | None,
    profile_company: str | None,
    profile_phone: str | None,
    profile_email: str | None,
) -> DuplicateCandidate | None:
    reasons: list[str] = []
    score = 0.0

    id_email = normalize_email(identity.email)
    id_phone = normalize_phone_digits(identity.phone)
    id_name = normalize_name(identity.name)
    id_company = normalize_company(identity.company)

    prof_email = normalize_email(profile_email)
    prof_phone = normalize_phone_digits(profile_phone)
    prof_name = normalize_name(profile_name)
    prof_company = normalize_company(profile_company)

    email_match = bool(id_email and prof_email and id_email == prof_email)
    phone_match = bool(id_phone and prof_phone and id_phone == prof_phone)
    name_match = bool(id_name and prof_name and id_name == prof_name)
    company_match = bool(id_company and prof_company and id_company == prof_company)

    if email_match:
        reasons.append("EMAIL_EXACT")
        score += SCORE_EMAIL_EXACT
    if phone_match:
        reasons.append("PHONE_EXACT")
        score += SCORE_PHONE_EXACT
    if name_match and company_match:
        reasons.append("NAME_COMPANY_EXACT")
        score += SCORE_NAME_COMPANY_EXACT
    elif name_match:
        reasons.append("NAME_EXACT")
        score += SCORE_NAME_EXACT
    elif company_match:
        reasons.append("COMPANY_EXACT")
        score += SCORE_COMPANY_EXACT

    if not reasons:
        return None

    # Explicit high-confidence overrides for exact contact matches.
    if email_match and phone_match:
        score = 1.0
    elif email_match and not phone_match:
        score = max(score, 0.85)
    elif phone_match and not email_match:
        score = max(score, 0.80)
    elif name_match and company_match:
        score = max(score, 0.65)

    score = min(1.0, score)
    return DuplicateCandidate(
        person_id=UUID(int=0),  # filled by caller
        name=profile_name or "",
        company=profile_company,
        match_reasons=tuple(reasons),
        score=score,
    )


class DuplicateMatcher:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_candidates(
        self, identity: IdentityExtraction
    ) -> list[DuplicateCandidate]:
        if not identity.has_any_signal():
            return []

        profiles = self._load_candidate_profiles(identity)
        results: list[DuplicateCandidate] = []
        for person_id, name, company, phone, email in profiles:
            scored = score_candidate(
                identity=identity,
                profile_name=name,
                profile_company=company,
                profile_phone=phone,
                profile_email=email,
            )
            if scored is None:
                continue
            results.append(
                DuplicateCandidate(
                    person_id=person_id,
                    name=scored.name,
                    company=scored.company,
                    match_reasons=scored.match_reasons,
                    score=scored.score,
                )
            )

        results.sort(
            key=lambda c: (-c.score, (c.name or "").casefold(), str(c.person_id))
        )
        return results[:MAX_DUPLICATE_CANDIDATES]

    def _load_candidate_profiles(
        self, identity: IdentityExtraction
    ) -> list[tuple[UUID, str | None, str | None, str | None, str | None]]:
        """Load non-deleted people that could match contact/name signals."""
        filters = []
        email = normalize_email(identity.email)
        phone = normalize_phone_digits(identity.phone)
        name = normalize_name(identity.name)
        company = normalize_company(identity.company)

        if email:
            filters.append(func.lower(func.trim(PersonProfile.email)) == email)
        if phone:
            # Strip non-digits in SQL for comparison.
            filters.append(
                func.regexp_replace(PersonProfile.phone, r"[^0-9]", "", "g") == phone
            )
        if name:
            filters.append(
                func.lower(func.regexp_replace(func.trim(PersonProfile.name), r"\s+", " ", "g"))
                == name
            )
        if company:
            filters.append(
                func.lower(
                    func.regexp_replace(
                        func.trim(PersonProfile.affiliation_company), r"\s+", " ", "g"
                    )
                )
                == company
            )

        if not filters:
            return []

        stmt = (
            select(
                Person.id,
                PersonProfile.name,
                PersonProfile.affiliation_company,
                PersonProfile.phone,
                PersonProfile.email,
            )
            .join(PersonProfile, PersonProfile.person_id == Person.id)
            .where(
                Person.deleted_at.is_(None),
                Person.status != "DELETED",
                or_(*filters),
            )
        )
        rows = self.db.execute(stmt).all()
        return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
