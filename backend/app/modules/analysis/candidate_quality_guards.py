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
    r"근무경력|재직기간|경력사항|직장명|근무처|"
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
    r"근무경력|재직기간|직장명|근무처|회사명|최종직급|담당업무|직위|재직"
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
_ONGOING_END_KEYS = frozenset({"현재", "재직중", "present"})


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
    """Match company text without treating short ASCII names as substrings.

    E.g. "KT" must not match "SKT" or "KTNetworks" on a separate employer
    row. OCR whitespace variance is tolerated for the text comparison.
    """
    needle = _fold(company.strip())
    if not needle:
        return False
    # Permit OCR whitespace inside company names, but preserve spaces around
    # each match so "KT 2020.01" is not mistaken for "KT2020.01".
    pattern = r"\\s*".join(re.escape(ch) for ch in needle)
    for match in re.finditer(pattern, line, flags=re.IGNORECASE):
        before = line[match.start() - 1] if match.start() else ""
        after = line[match.end()] if match.end() < len(line) else ""
        if (
            needle[0].isascii()
            and needle[0].isalnum()
            and before.isascii()
            and before.isalnum()
        ):
            continue
        if (
            needle[-1].isascii()
            and needle[-1].isalnum()
            and after.isascii()
            and after.isalnum()
        ):
            continue
        return True
    return False


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


def _classify_line_context(line: str, *, section: str) -> str:
    """Return employment | project | ambiguous for a company-bearing line.

    Document type (including DOC-KOSA) is never enough by itself — KOSA
    project-performance customer rows must stay project context.
    """
    is_project_row = bool(
        _PROJECT_SECTION_RE.search(line) or _PROJECT_ROW_RE.search(line)
    )
    is_employment_row = bool(
        _EMPLOYMENT_SECTION_RE.search(line) or _EMPLOYMENT_ROW_RE.search(line)
    )
    # Flattened PDF/OCR text may place employer and project data on one line.
    # Such mixed evidence is not enough to remove an employment candidate.
    if is_project_row and is_employment_row:
        return "ambiguous"
    if is_project_row:
        return "project"
    if is_employment_row:
        return "employment"
    if section == "project":
        return "project"
    if section == "employment":
        return "employment"
    # A company and a date alone occur frequently in project tables and do
    # not establish an employment relationship.
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


def _is_ongoing_end(value: str | None) -> bool:
    text = (value or "").strip().casefold().replace(" ", "")
    return text in _ONGOING_END_KEYS


def _linked_source_ends(
    emp_dates: list[tuple[str | None, str | None]],
    *,
    candidate_start: str | None,
) -> list[str]:
    """Ends from employer-table ranges whose start matches the candidate start.

    Distinct employments at the same company (different starts) are ignored.
    """
    start = (candidate_start or "").strip()
    if not start:
        return []
    ends: list[str] = []
    for src_start, src_end in emp_dates:
        if not src_start or not src_end or _is_ongoing_end(src_end):
            continue
        if not dates_compatible(start, src_start):
            continue
        ends.append(src_end)
    return ends


def _unique_incompatible_ends(ends: list[str]) -> list[str]:
    """Collapse precision-compatible ends; return mutually incompatible set."""
    unique: list[str] = []
    for end in ends:
        matched = False
        for i, kept in enumerate(unique):
            if dates_compatible(end, kept):
                # Keep the more precise token when compatible.
                score_new = sum(ch.isdigit() for ch in end) + end.count(".")
                score_old = sum(ch.isdigit() for ch in kept) + kept.count(".")
                if score_new > score_old:
                    unique[i] = end
                matched = True
                break
        if not matched:
            unique.append(end)
    return unique


def _resolve_end_date_for_review(
    *,
    candidate_end: str | None,
    linked_ends: list[str],
) -> str | None:
    """Return REVIEW conflict token when ends disagree; never pick a winner."""
    cand_end = (candidate_end or "").strip()
    if cand_end and _EMPLOYMENT_END_CONFLICT_SEP in cand_end:
        return cand_end
    unique_ends = _unique_incompatible_ends(linked_ends)
    if not unique_ends:
        return None
    if len(unique_ends) > 1:
        # Multiple incompatible source ends for the same start — ambiguous.
        token = unique_ends[0]
        for extra in unique_ends[1:]:
            token = _employment_end_conflict_token(token, extra)
        if cand_end and all(
            not dates_compatible(cand_end, src) for src in unique_ends
        ):
            token = _employment_end_conflict_token(token, cand_end)
        return token
    source_end = unique_ends[0]
    if not cand_end:
        return None
    if dates_compatible(cand_end, source_end):
        return None
    return _employment_end_conflict_token(cand_end, source_end)


def _guard_employment_history(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> list[EmploymentCandidate]:
    """Drop clear project-customer-only employers; REVIEW start-linked end conflicts.

    Conservative: insufficient/ambiguous row structure never deletes a row.
    KOSA employer-table rows are preserved; KOSA project customers are not
    treated as employment evidence.
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
        has_project_evidence = False
        has_ambiguous_evidence = False

        for _doc_id, _page_no, section, line, _doc_type in context_lines:
            if not _line_has_company(line, company):
                continue
            kind = _classify_line_context(line, section=section)
            if kind == "employment":
                has_employment_evidence = True
                emp_dates.extend(_extract_range_dates(line))
            elif kind == "project":
                has_project_evidence = True
            else:
                has_ambiguous_evidence = True

        is_customer_name = company_key in customer_keys
        # Drop only when project-customer mix-in is positively evidenced and
        # no employer-table evidence exists. Ambiguous/missing structure → keep.
        if (
            is_customer_name
            and has_project_evidence
            and not has_employment_evidence
            and not has_ambiguous_evidence
        ):
            continue

        updated = row
        linked_ends = _linked_source_ends(
            emp_dates, candidate_start=row.start_date
        )
        review_end = _resolve_end_date_for_review(
            candidate_end=row.end_date, linked_ends=linked_ends
        )
        if review_end is not None:
            updated = row.model_copy(update={"end_date": review_end})
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
