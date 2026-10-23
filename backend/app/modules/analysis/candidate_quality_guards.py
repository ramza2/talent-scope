"""Conservative post-extraction guards for clearly misplaced candidate values.

Run only on newly extracted candidates, before Diff creation. Never changes
Confirmed data or existing analysis runs, and never infers missing facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    r"근무경력|재직기간|경력사항|직장명|근무처|소속회사|근무기관|재직회사|"
    r"회사명\s*기간\s*직위|최종직급|입사\s*일|퇴사\s*일"
)
_PROJECT_SECTION_RE = re.compile(
    r"프로젝트\s*수행내역|프로젝트\s*경력|수행내역|참여사업|수행사업|"
    r"경력기술서|경력기술|발주처|발주기관|고객사|사업명|수행역할|업무내용|"
    r"수행\s*프로젝트"
)
_PROJECT_ROW_RE = re.compile(
    r"발주처|발주기관|고객사|사업명|프로젝트\s*수행|수행내역|정보화|"
    r"시스템\s*구축|BIA|BCP|이전통합"
)
# Bare "프로젝트" alone is too weak for row classification — it often appears
# as the start of a following section header on collapsed KOSA lines.
_EMPLOYMENT_ROW_RE = re.compile(
    r"근무경력|재직기간|직장명|근무처|소속회사|근무기관|재직회사|"
    r"회사명|최종직급|담당업무|직위|재직"
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
# Unparseable multi-value end_date → diff REVIEW. Both alternatives stay visible
# for humans; we never overwrite with a chosen calendar date.
_EMPLOYMENT_END_CONFLICT_SEP = " / "
_ONGOING_END_KEYS = frozenset({"현재", "재직중", "present"})
_PROFILE_LIKE_DOC_TYPES = frozenset(
    {"DOC-PROFILE", "DOC-RESUME", "DOC-CAREER"}
)


@dataclass(frozen=True)
class _ContextSpan:
    doc_id: str
    page_no: int
    section: str
    text: str
    doc_type: str | None


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


def _is_company_continue_char(ch: str) -> bool:
    """Letter/Hangul that can extend a company token (digits/punct do not)."""
    if "가" <= ch <= "힣":
        return True
    return ch.isalpha()


def _line_has_company(line: str, company: str) -> bool:
    """True when company appears as a bounded token (KT ⊄ SKT).

    Folded matching ignores whitespace so `피엔드티컨설팅(주) 2010.03` still
    hits; digits/punctuation after the name are valid boundaries (dates).
    """
    needle = _fold(company.strip())
    if not needle:
        return False
    hay = _fold(line)
    start = 0
    while True:
        idx = hay.find(needle, start)
        if idx < 0:
            return False
        before_ok = idx == 0 or not _is_company_continue_char(hay[idx - 1])
        after_idx = idx + len(needle)
        after_ok = (
            after_idx >= len(hay)
            or not _is_company_continue_char(hay[after_idx])
        )
        if before_ok and after_ok:
            return True
        start = idx + 1


def _classify_span(text: str, *, section: str) -> str:
    """Classify a span using sticky section context; never doc-type alone."""
    has_emp_section = bool(_EMPLOYMENT_SECTION_RE.search(text))
    has_proj_section = bool(_PROJECT_SECTION_RE.search(text))
    has_emp_row = bool(_EMPLOYMENT_ROW_RE.search(text))
    has_proj_row = bool(_PROJECT_ROW_RE.search(text))

    # Explicit section headers win; mixed supply of both stays sticky-driven.
    if has_proj_section and not has_emp_section:
        return "project"
    if has_emp_section and not has_proj_section:
        return "employment"
    if section == "project":
        return "project"
    if has_proj_row and not has_emp_row and not has_emp_section:
        return "project"
    if has_emp_row or section == "employment":
        # Dates / employer-table rows inside an employer section count.
        return "employment"
    # Unknown section: dated company lines without project cues are weak
    # employment-shaped, but only when page prior is not project.
    if (
        section == "unknown"
        and _DATE_TOKEN_RE.search(text)
        and not has_proj_row
    ):
        return "employment"
    return "ambiguous"


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


def _default_page_section(doc_type: str | None, page_no: int) -> str:
    """Document/page prior when explicit section headers are absent."""
    dtype = (doc_type or "").strip().upper()
    if dtype in _PROFILE_LIKE_DOC_TYPES:
        # Typical career DOCX: p1 employer table, later pages project history.
        return "employment" if page_no <= 1 else "project"
    return "unknown"


def _split_collapsed_line(line: str, *, page_section: str) -> list[tuple[str, str]]:
    """Split KOSA-style collapsed lines into section-tagged spans."""
    # Collect (start, end, section), keep longest-leftmost non-overlapping markers
    # so "프로젝트 수행내역" is not chopped into a trailing bare "프로젝트" stub.
    raw: list[tuple[int, int, str]] = []
    for match in _EMPLOYMENT_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "employment"))
    for match in _PROJECT_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "project"))
    if not raw:
        return [(page_section, line)]
    raw.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    markers: list[tuple[int, int, str]] = []
    for start, end, section in raw:
        if markers and start < markers[-1][1]:
            continue
        markers.append((start, end, section))
    spans: list[tuple[str, str]] = []
    if markers[0][0] > 0:
        spans.append((page_section, line[: markers[0][0]].strip()))
    for index, (start, _end, section) in enumerate(markers):
        cut = markers[index + 1][0] if index + 1 < len(markers) else len(line)
        piece = line[start:cut].strip()
        if piece:
            spans.append((section, piece))
    return [(section, text) for section, text in spans if text]


def _iter_context_spans(
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> Iterable[_ContextSpan]:
    if not page_texts:
        return
    type_map = document_type_by_id or {}
    for (doc_id, page_no), text in page_texts.items():
        doc_type = type_map.get(str(doc_id))
        page_section = _default_page_section(doc_type, int(page_no))
        sticky = page_section
        raw_text = str(text or "")
        lines = raw_text.splitlines() or ([raw_text] if raw_text.strip() else [])
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            for section_hint, span_text in _split_collapsed_line(
                line, page_section=sticky
            ):
                if _EMPLOYMENT_SECTION_RE.search(span_text):
                    sticky = "employment"
                    section = "employment"
                elif _PROJECT_SECTION_RE.search(span_text):
                    sticky = "project"
                    section = "project"
                else:
                    section = section_hint if section_hint != "unknown" else sticky
                yield _ContextSpan(
                    doc_id=str(doc_id),
                    page_no=int(page_no),
                    section=section,
                    text=span_text,
                    doc_type=doc_type,
                )


def _is_ongoing_end(value: str | None) -> bool:
    text = (value or "").strip().casefold().replace(" ", "")
    return text in _ONGOING_END_KEYS


def _linked_source_ends(
    emp_dates: list[tuple[str | None, str | None]],
    *,
    candidate_start: str | None,
) -> list[str]:
    """Ends from employer-table ranges whose start matches the candidate start."""
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
    """Return REVIEW conflict token when ends disagree; never pick a winner.

    Joined unparseable tokens keep both alternatives visible and trip the
    existing employment date REVIEW path. We do not invent a chosen date.
    """
    cand_end = (candidate_end or "").strip()
    if cand_end and _EMPLOYMENT_END_CONFLICT_SEP in cand_end:
        return cand_end
    unique_ends = _unique_incompatible_ends(linked_ends)
    if not unique_ends:
        return None
    if len(unique_ends) > 1:
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


def _matches_project_customer_period(
    row: EmploymentCandidate,
    projects: list[ProjectCandidate],
) -> bool:
    """True when employment dates mirror a project row for the same customer."""
    company_key = _key(row.company_name)
    if company_key is None:
        return False
    for project in projects:
        if _key(project.customer_name) != company_key:
            continue
        if not dates_compatible(row.start_date, project.start_date):
            continue
        # End may be omitted on either side; compatible ends strengthen the hit.
        if row.end_date and project.end_date:
            if not dates_compatible(row.end_date, project.end_date):
                continue
        return True
    return False


def _guard_employment_history(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> list[EmploymentCandidate]:
    """Drop certain project-customer mix-ins; REVIEW start-linked end conflicts.

    Uses document/page/section spans (including collapsed KOSA lines). Project
    page dates never count as employer-table evidence. Ambiguous rows are kept.
    """
    rows = list(candidate.employment_history or [])
    if not rows:
        return rows
    if not page_texts:
        return rows

    projects = list(candidate.projects or [])
    customer_keys = {
        key
        for project in projects
        if (key := _key(project.customer_name)) is not None
    }
    spans = list(_iter_context_spans(page_texts, document_type_by_id))
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

        for span in spans:
            if not _line_has_company(span.text, company):
                continue
            kind = _classify_span(span.text, section=span.section)
            if kind == "employment":
                has_employment_evidence = True
                emp_dates.extend(_extract_range_dates(span.text))
            elif kind == "project":
                has_project_evidence = True

        is_customer_name = company_key in customer_keys
        mirrors_project = _matches_project_customer_period(row, projects)
        # Certain mix-in only: project customer with project-page/period evidence
        # and no employer-table / KOSA employment-section evidence.
        if (
            is_customer_name
            and not has_employment_evidence
            and (has_project_evidence or mirrors_project)
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
