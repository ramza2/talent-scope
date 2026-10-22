"""Conservative post-extraction guards for clearly misplaced candidate values.

Run only on newly extracted candidates, before Diff creation. Never changes
Confirmed data or existing analysis runs, and never infers missing facts.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.ai.schemas.profile_candidate import (
    EmploymentCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
)
from app.core.person_name import is_korean_person_name, person_name_match_key
from app.modules.analysis.diff_engine import dates_compatible, parse_date_parts


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

_EMPLOYMENT_SECTION_RE = re.compile(
    r"근무경력|재직기간|경력사항|직장명|"
    r"회사명\s*기간\s*직위|최종직급|입사\s*일|퇴사\s*일"
)
_PROJECT_SECTION_RE = re.compile(
    r"수행내역|참여사업|수행사업|프로젝트\s*경력|경력기술|"
    r"발주처|고객사|사업명|수행역할|업무내용"
)
_PROJECT_ROW_RE = re.compile(
    r"발주처|고객사|사업명|프로젝트|수행내역|정보화|시스템\s*구축|"
    r"BIA|BCP|이전통합"
)
_EMPLOYMENT_ROW_RE = re.compile(
    r"근무경력|재직기간|직장명|회사명|최종직급|담당업무|직위|재직"
)
_DATE_TOKEN_RE = re.compile(
    r"(?:19|20)\d{2}[./\-]\d{1,2}(?:[./\-]\d{1,2})?"
)
_DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN_RE.pattern})"
    r"\s*[~\-–—至到]\s*"
    rf"(?P<end>현재|재직\s*중|재직중|Present|PRESENT|{_DATE_TOKEN_RE.pattern})",
    re.IGNORECASE,
)
_EMPLOYMENT_END_CONFLICT_SEP = " / "
_KOSA_DOC_TYPES = frozenset({"DOC-KOSA"})


def _key(value: str | None) -> str | None:
    if not value:
        return None
    folded = "".join(value.split()).casefold()
    return folded or None


def _fold(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace())


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


def _normalize_date_token(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.casefold() in {"현재", "재직중", "재직 중", "present"}:
        return text.replace(" ", "")
    parts = parse_date_parts(text.replace(".", "-").replace("/", "-"))
    if parts is None:
        return text
    year, month, day = parts
    if month is None:
        return f"{year:04d}"
    if day is None:
        return f"{year:04d}.{month:02d}"
    return f"{year:04d}.{month:02d}.{day:02d}"


def _employment_end_conflict_token(a: str, b: str) -> str:
    parts: list[str] = []
    for raw in (a, b):
        text = (raw or "").strip()
        if not text:
            continue
        if _EMPLOYMENT_END_CONFLICT_SEP in text:
            parts.extend(
                p.strip()
                for p in text.split(_EMPLOYMENT_END_CONFLICT_SEP)
                if p.strip()
            )
        else:
            parts.append(text)
    unique = sorted(dict.fromkeys(parts), key=lambda s: s.casefold())
    return _EMPLOYMENT_END_CONFLICT_SEP.join(unique)


def _line_has_company(line: str, company: str) -> bool:
    needle = _fold(company.strip())
    if not needle:
        return False
    return needle in _fold(line)


def _extract_range_dates(line: str) -> list[tuple[str | None, str | None]]:
    out: list[tuple[str | None, str | None]] = []
    for match in _DATE_RANGE_RE.finditer(line):
        out.append(
            (
                _normalize_date_token(match.group("start")),
                _normalize_date_token(match.group("end")),
            )
        )
    return out


def _classify_line_context(
    line: str,
    *,
    section: str,
    doc_type: str | None,
) -> str:
    """Return employment | project | ambiguous for a company-bearing line."""
    if doc_type and doc_type.strip().upper() in _KOSA_DOC_TYPES:
        return "employment"
    if _EMPLOYMENT_SECTION_RE.search(line) or _EMPLOYMENT_ROW_RE.search(line):
        return "employment"
    if section == "employment" and not _PROJECT_ROW_RE.search(line):
        return "employment"
    if _PROJECT_SECTION_RE.search(line) or _PROJECT_ROW_RE.search(line):
        return "project"
    if section == "project":
        return "project"
    # Company + period without project markers → employment-table shaped.
    if _DATE_TOKEN_RE.search(line) and not _PROJECT_ROW_RE.search(line):
        return "employment"
    return "ambiguous"


def _iter_context_lines(
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> Iterable[tuple[str, int, str, str, str | None]]:
    if not page_texts:
        return
    type_map = document_type_by_id or {}
    for (doc_id, page_no), text in page_texts.items():
        section = "unknown"
        doc_type = type_map.get(str(doc_id))
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if _EMPLOYMENT_SECTION_RE.search(line):
                section = "employment"
            elif _PROJECT_SECTION_RE.search(line):
                section = "project"
            yield str(doc_id), int(page_no), section, line, doc_type


def _guard_employment_history(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> list[EmploymentCandidate]:
    """Drop project-customer-only employers; REVIEW conflicting employment ends.

    Never deletes KOSA-evidenced employment rows. Does not invent employers.
    """
    rows = list(candidate.employment_history or [])
    if not rows:
        return rows
    # Without page text we cannot distinguish employer tables from project
    # customers — leave employment unchanged (fail open).
    if not page_texts:
        return rows

    customer_keys = {
        key
        for project in candidate.projects or []
        if (key := _key(project.customer_name)) is not None
    }
    context_lines = list(_iter_context_lines(page_texts, document_type_by_id))
    kept: list[EmploymentCandidate] = []

    for row in rows:
        company = (row.company_name or "").strip()
        company_key = _key(company)
        if not company_key:
            kept.append(row)
            continue

        emp_dates: list[tuple[str | None, str | None]] = []
        has_employment_evidence = False
        has_kosa_evidence = False
        has_project_only_evidence = False

        for doc_id, _page_no, section, line, doc_type in context_lines:
            if not _line_has_company(line, company):
                continue
            kind = _classify_line_context(
                line, section=section, doc_type=doc_type
            )
            if kind == "employment":
                has_employment_evidence = True
                if doc_type and doc_type.strip().upper() in _KOSA_DOC_TYPES:
                    has_kosa_evidence = True
                emp_dates.extend(_extract_range_dates(line))
            elif kind == "project":
                has_project_only_evidence = True
            elif doc_type and doc_type.strip().upper() in _KOSA_DOC_TYPES:
                has_kosa_evidence = True
                has_employment_evidence = True
                emp_dates.extend(_extract_range_dates(line))

        is_customer_name = company_key in customer_keys
        if (
            is_customer_name
            and not has_employment_evidence
            and not has_kosa_evidence
        ):
            # Project customer label alone must not become an employer.
            continue
        if (
            has_project_only_evidence
            and not has_employment_evidence
            and not has_kosa_evidence
            and is_customer_name
        ):
            continue

        updated = row
        # Prefer employer-table end dates for conflict detection only.
        source_ends = [
            end
            for _start, end in emp_dates
            if end and end.casefold() not in {"현재", "재직중", "present"}
        ]
        cand_end = (row.end_date or "").strip()
        if (
            source_ends
            and cand_end
            and _EMPLOYMENT_END_CONFLICT_SEP not in cand_end
        ):
            incompatible = [
                src
                for src in source_ends
                if not dates_compatible(cand_end, src)
            ]
            if incompatible:
                # Do not pick AI or source — force REVIEW via unparseable token.
                updated = row.model_copy(
                    update={
                        "end_date": _employment_end_conflict_token(
                            cand_end, incompatible[0]
                        )
                    }
                )
        kept.append(updated)

    return kept


def apply_candidate_quality_guards(
    candidate: ProfileCandidateDocument,
    *,
    confirmed_person_name: str | None = None,
    page_texts: dict[tuple[str, int], str] | None = None,
    document_type_by_id: dict[str, str] | None = None,
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

    person_keys = {
        person_name_match_key(name)
        for name in (profile.name, confirmed_person_name)
        if is_korean_person_name(name)
    }
    customer_keys = {
        key
        for project in candidate.projects
        if (key := _key(project.customer_name)) is not None
    }
    kept_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        name = project.project_name
        # Person's own name is a document/header identity, not a project.
        if (
            person_keys
            and is_korean_person_name(name)
            and person_name_match_key(name) in person_keys
        ):
            continue

        # A bare customer organization label repeated in a project's customer
        # field is not itself a project; retain rows with independent details.
        if (
            _key(name) in customer_keys
            and _no_independent_project_details(project)
        ):
            continue
        kept_projects.append(project)

    kept_employment = _guard_employment_history(
        candidate,
        page_texts=page_texts,
        document_type_by_id=document_type_by_id,
    )

    if (
        profile is candidate.profile
        and len(kept_projects) == len(candidate.projects)
        and kept_employment == list(candidate.employment_history or [])
    ):
        return candidate
    return candidate.model_copy(
        update={
            "profile": profile,
            "projects": kept_projects,
            "employment_history": kept_employment,
        }
    )
