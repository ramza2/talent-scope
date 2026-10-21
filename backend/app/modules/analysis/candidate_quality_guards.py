"""Conservative post-extraction guards for clearly misplaced candidate values.

Run only on newly extracted candidates, before Diff creation. Never changes
Confirmed data or existing analysis runs, and never infers missing facts.
"""

from __future__ import annotations

import re

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate
from app.core.person_name import is_korean_person_name, person_name_match_key


# One plain mailbox, not an address/phone/name/collection pasted into email.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)

# Total career length belongs in career_document_value, never employment_type.
_CAREER_DURATION_RE = re.compile(
    r"(?:총\s*)?(?:경력\s*:?\s*)?"
    r"(?:\d+\s*년(?:\s*\d+\s*개월)?|\d+\s*개월)"
)


def _key(value: str | None) -> str | None:
    if not value:
        return None
    folded = "".join(value.split()).casefold()
    return folded or None


def _no_independent_project_details(project: ProjectCandidate) -> bool:
    # A customer label alone is not a separate project when another actual
    # project already identifies it as its customer. Never drop dated/detail rows.
    return not any(
        (
            project.start_date,
            project.end_date,
            project.responsibilities,
            project.project_summary,
            project.jobs,
            project.skills,
            project.expertise,
            project.business_domains,
            project.customer_types,
        )
    )


def apply_candidate_quality_guards(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """Drop unambiguous field leakage and header/customer-only pseudo-projects.

    This intentionally does NOT perform broad fuzzy matching, contact
    discovery, employment-type classification, or project-title guessing.
    """
    profile = candidate.profile
    cleared: set[str] = set()
    updates: dict[str, object] = {}

    email = profile.email
    if email is not None and not _EMAIL_RE.fullmatch(email.strip()):
        updates["email"] = None
        cleared.add("email")

    employment_type = profile.employment_type
    if (
        employment_type is not None
        and _CAREER_DURATION_RE.fullmatch(employment_type.strip())
    ):
        updates["employment_type"] = None
        cleared.add("employment_type")

    if cleared:
        updates["source_refs"] = {
            key: refs
            for key, refs in profile.source_refs.items()
            if key not in cleared
        }
        profile = profile.model_copy(update=updates)

    person_key = (
        person_name_match_key(profile.name)
        if is_korean_person_name(profile.name)
        else None
    )
    customer_keys = {
        key
        for project in candidate.projects
        if (key := _key(project.customer_name)) is not None
    }
    kept: list[ProjectCandidate] = []
    for project in candidate.projects:
        name = project.project_name
        # Person's own name is a document/header identity, not a project.
        if (
            person_key
            and is_korean_person_name(name)
            and person_name_match_key(name) == person_key
        ):
            continue

        # A bare customer organization label repeated in a project's customer
        # field is not itself a project; retain rows with independent details.
        if (
            _key(name) in customer_keys
            and _no_independent_project_details(project)
        ):
            continue
        kept.append(project)

    if profile is candidate.profile and len(kept) == len(candidate.projects):
        return candidate
    return candidate.model_copy(update={"profile": profile, "projects": kept})
