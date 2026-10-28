"""Conservative post-extraction guards for clearly misplaced candidate values.

Run only on newly extracted candidates, before Diff creation. Never changes
Confirmed data or existing analysis runs, and never infers missing facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from app.ai.schemas.profile_candidate import (
    CertificationCandidate,
    EducationCandidate,
    EmploymentCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)
from app.core.person_name import is_korean_person_name, person_name_match_key
from app.modules.analysis.confirm_dates import is_ongoing_end_marker
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
# Avoid bare "전공"/"학위" — they appear inside majors like 유전공학.
_EDUCATION_SECTION_RE = re.compile(
    r"학력사항|교육사항|학력|학교명|전공\s*명|학위\s*명|입학\s*일|졸업\s*일"
)
_CERT_SECTION_RE = re.compile(
    r"자격증|자격면허|자격\s*사항|자격명|취득일|발급기관|면허"
)
# Shape cue for certification titles (not used as fuzzy match / distance).
_CERT_NAME_SHAPE_RE = re.compile(
    r"(기사|기술사|산업기사|기능사|프로그래머|관리사|감리사)"
)
_CERT_HEADER_ONLY_RE = re.compile(
    r"^(자격증|자격면허|자격\s*사항|자격명|취득일|발급기관|면허)$"
)
_DATE_TOKEN_RE = re.compile(
    r"(?:19|20)\d{2}[./\-]\d{1,2}(?:[./\-]\d{1,2})?"
)
# Known KOSA/OCR confusions only — never general string similarity.
_CERT_OCR_CANONICAL: tuple[tuple[str, str], ...] = (
    ("경보처리기사", "정보처리기사"),
    ("전자계산기조작용용기사", "전자계산기조직응용기사"),
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
# DOCX employer-table column headers (not company-name cells).
_EMPLOYMENT_HEADER_CELL_RE = re.compile(
    r"^(?:회사명|기간|직위|담당업무|근무처|직장명|소속회사|근무기관|재직회사|"
    r"최종직급|입사\s*일|퇴사\s*일|근무경력|재직기간|경력사항)$"
)
# Korean corporate forms: leading/trailing on folded Hangul/token text.
_CORP_SUFFIX_KO_RE = re.compile(
    r"(?:주식회사|유한책임회사|유한회사|\(주\)|㈜|\(유\)|\(사\))$"
)
_CORP_PREFIX_KO_RE = re.compile(
    r"^(?:주식회사|유한책임회사|유한회사|\(주\)|㈜|\(유\)|\(사\))"
)
# English corporate forms: independent trailing tokens only (not Zinc→Z).
_CORP_SUFFIX_EN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:co\.?,?\s*ltd\.?|corp\.?|inc\.?|llc\.?)\s*$"
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

    Boundaries are checked on the original line so section headers like
    ``학력 한림대학교`` do not glue into one Hangul run after space-folding.
    Digits after the name remain valid (dates). A space-folded fallback still
    allows ``피엔드티 컨설팅`` ↔ ``피엔드티컨설팅`` when letter-bounded.
    """
    needle = company.strip()
    if not needle:
        return False
    esc = re.escape(needle)
    # Letter/Hangul boundaries; digits after the token are allowed (dates).
    if re.search(rf"(?<![0-9A-Za-z가-힣]){esc}(?![A-Za-z가-힣])", line):
        return True
    folded_needle = _fold(needle)
    folded_line = _fold(line)
    if not folded_needle or folded_needle not in folded_line:
        return False
    # Fallback only when needle itself contains whitespace that folding removes.
    if not any(ch.isspace() for ch in needle):
        return False
    start = 0
    while True:
        idx = folded_line.find(folded_needle, start)
        if idx < 0:
            return False
        before_ok = idx == 0 or not _is_company_continue_char(
            folded_line[idx - 1]
        )
        after_idx = idx + len(folded_needle)
        after_ok = (
            after_idx >= len(folded_line)
            or not _is_company_continue_char(folded_line[after_idx])
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
    # Unknown + date alone is never employment evidence (KOSA p2~3 project
    # rows often lack headers; treating dates as employers causes mix-ins).
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
    if dtype == "DOC-KOSA":
        # KOSA p2+ continues 수행내역 even when the header is omitted.
        return "unknown" if page_no <= 1 else "project"
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
    # Carry explicit section sticky across later unknown pages of the same doc
    # (e.g. KOSA p1 ends in 수행내역 → headerless p2/p3 stay project).
    sticky_by_doc: dict[str, str] = {}
    ordered = sorted(
        page_texts.items(),
        key=lambda item: (str(item[0][0]), int(item[0][1])),
    )
    for (doc_id, page_no), text in ordered:
        doc_key = str(doc_id)
        doc_type = type_map.get(doc_key)
        page_section = _default_page_section(doc_type, int(page_no))
        if page_section == "unknown":
            sticky = sticky_by_doc.get(doc_key, "unknown")
        else:
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
                    doc_id=doc_key,
                    page_no=int(page_no),
                    section=section,
                    text=span_text,
                    doc_type=doc_type,
                )
        if sticky != "unknown":
            sticky_by_doc[doc_key] = sticky


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


@dataclass(frozen=True)
class _KosaExactEmploymentPeriod:
    """One exact-day KOSA employment-section period for a single employer."""

    doc_id: str
    page_no: int
    quote_text: str
    start: str
    end: str

    @property
    def key(self) -> tuple[str, int, str, str, str]:
        return (
            self.doc_id,
            self.page_no,
            self.start,
            self.end,
            _fold(self.quote_text),
        )


def _exact_calendar_day(token: str | None) -> date | None:
    """Return a calendar date only for exact YYYY.MM.DD (or -/ /) tokens."""
    text = (token or "").strip()
    if not text:
        return None
    parts = parse_date_parts(text.replace(".", "-").replace("/", "-"))
    if parts is None:
        return None
    year, month, day = parts
    if month is None or day is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _collect_kosa_exact_day_employment_periods(
    spans: list[_ContextSpan],
    *,
    company: str,
) -> list[_KosaExactEmploymentPeriod]:
    """Independent same-company exact-day periods from KOSA 근무경력 only.

    Project/수행내역 spans are never used. Ambiguous multi-range lines are
    skipped. Company match is conservative (``_company_match_key`` / bounded).
    """
    raw = (company or "").strip()
    stem = _company_match_key(raw)
    if not raw or not stem:
        return []
    out: list[_KosaExactEmploymentPeriod] = []
    seen: set[tuple[str, int, str, str, str]] = set()
    for span in spans:
        if (span.doc_type or "").strip().upper() != "DOC-KOSA":
            continue
        if _classify_span(span.text, section=span.section) != "employment":
            continue
        if not _span_matches_employment_company(span.text, raw=raw, stem=stem):
            continue
        ranges = _extract_range_dates(span.text)
        if len(ranges) != 1:
            continue
        start_tok, end_tok = ranges[0]
        if (
            _exact_calendar_day(start_tok) is None
            or _exact_calendar_day(end_tok) is None
        ):
            continue
        assert start_tok is not None and end_tok is not None
        start_d = _exact_calendar_day(start_tok)
        end_d = _exact_calendar_day(end_tok)
        assert start_d is not None and end_d is not None
        if end_d < start_d:
            continue
        quote = span.text.strip()
        if len(quote) > 500:
            quote = quote[:500]
        period = _KosaExactEmploymentPeriod(
            doc_id=span.doc_id,
            page_no=span.page_no,
            quote_text=quote,
            start=_normalize_date_token(start_tok) or start_tok,
            end=_normalize_date_token(end_tok) or end_tok,
        )
        if period.key in seen:
            continue
        seen.add(period.key)
        out.append(period)
    return out


def _contiguous_kosa_chain_from_start(
    periods: list[_KosaExactEmploymentPeriod],
    *,
    candidate_start: str | None,
) -> list[_KosaExactEmploymentPeriod] | None:
    """Build a ≥2-segment day-contiguous chain that starts at candidate_start.

    Contiguity requires ``prev.end + 1 day == next.start``. Gaps, overlaps, or
    branching next segments refuse resolution.
    """
    start_day = _exact_calendar_day(candidate_start)
    if start_day is None or len(periods) < 2:
        return None

    def _sort_key(period: _KosaExactEmploymentPeriod) -> tuple[date, date]:
        start = _exact_calendar_day(period.start)
        end = _exact_calendar_day(period.end)
        assert start is not None and end is not None
        return (start, end)

    ordered = sorted(periods, key=_sort_key)
    anchors = [p for p in ordered if _exact_calendar_day(p.start) == start_day]
    if len(anchors) != 1:
        return None
    chain = [anchors[0]]
    remaining = [p for p in ordered if p.key != anchors[0].key]
    while remaining:
        last_end = _exact_calendar_day(chain[-1].end)
        assert last_end is not None
        expect = last_end + timedelta(days=1)
        nxt_matches = [
            p for p in remaining if _exact_calendar_day(p.start) == expect
        ]
        if not nxt_matches:
            break
        if len(nxt_matches) != 1:
            return None
        nxt = nxt_matches[0]
        chain.append(nxt)
        remaining = [p for p in remaining if p.key != nxt.key]
    if len(chain) < 2:
        return None
    return chain


def _end_date_conflict_alternatives(
    candidate_end: str | None,
    linked_ends: list[str],
) -> list[str]:
    """Collect distinct end tokens that participate in a conflict decision."""
    alts: list[str] = []
    cand = (candidate_end or "").strip()
    if cand and _EMPLOYMENT_END_CONFLICT_SEP in cand:
        alts.extend(
            part.strip()
            for part in cand.split(_EMPLOYMENT_END_CONFLICT_SEP)
            if part.strip()
        )
    elif cand and not _is_ongoing_end(cand):
        alts.append(cand)
    for raw in linked_ends:
        text = (raw or "").strip()
        if text and not _is_ongoing_end(text):
            alts.append(text)
    unique: list[str] = []
    seen: set[str] = set()
    for alt in alts:
        normalized = _normalize_date_token(alt) or alt
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def _alternatives_explained_by_kosa_chain(
    alternatives: list[str],
    chain: list[_KosaExactEmploymentPeriod],
) -> bool:
    """True when every alternative is a verified chain segment end."""
    chain_ends = {
        (_normalize_date_token(period.end) or period.end).casefold()
        for period in chain
    }
    if not alternatives:
        return False
    for alt in alternatives:
        key = (_normalize_date_token(alt) or alt).casefold()
        if key not in chain_ends:
            return False
    return True


def _try_resolve_kosa_contiguous_employment_end(
    *,
    company: str,
    candidate_start: str | None,
    candidate_end: str | None,
    linked_ends: list[str],
    spans: list[_ContextSpan],
) -> tuple[str, list[SourceRef]] | None:
    """Resolve end_date to the final contiguous KOSA period end when safe.

    Only same-company, exact-day, end+1=next.start KOSA 근무경력 chains qualify.
    Existing conflict tokens are eligible when every alternative is a chain
    segment end. Independent unexplained ends keep REVIEW (return None).
    """
    periods = _collect_kosa_exact_day_employment_periods(spans, company=company)
    chain = _contiguous_kosa_chain_from_start(
        periods, candidate_start=candidate_start
    )
    if chain is None:
        return None
    final_end = _normalize_date_token(chain[-1].end) or chain[-1].end
    alternatives = _end_date_conflict_alternatives(candidate_end, linked_ends)
    if alternatives:
        if not _alternatives_explained_by_kosa_chain(alternatives, chain):
            return None
    else:
        cand = (candidate_end or "").strip()
        if cand and not _is_ongoing_end(cand):
            cand_norm = (_normalize_date_token(cand) or cand).casefold()
            chain_ends = {
                (_normalize_date_token(p.end) or p.end).casefold() for p in chain
            }
            if cand_norm not in chain_ends and cand_norm != final_end.casefold():
                return None
    refs: list[SourceRef] = []
    seen: set[tuple[str, int, str]] = set()
    for period in chain:
        key = (period.doc_id, period.page_no, period.quote_text)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            SourceRef(
                document_id=period.doc_id,
                page_no=period.page_no,
                quote_text=period.quote_text,
            )
        )
    return final_end, refs


def _resolve_end_date_for_review(
    *,
    candidate_end: str | None,
    linked_ends: list[str],
) -> str | None:
    """Return REVIEW conflict token when ends disagree; never pick a winner.

    Joined unparseable tokens keep both alternatives visible and trip the
    existing employment date REVIEW path. We do not invent a chosen date.
    Contiguous KOSA chains are resolved earlier in ``_guard_employment_history``.
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


def _company_match_key(name: str | None) -> str | None:
    """Canonical employer key with conservative corporate-form stripping.

    - Korean ``(주)`` / ``주식회사`` etc. strip only as leading/trailing forms
      on the whitespace-folded token.
    - English ``Inc`` / ``Corp`` / ``LLC`` / ``Co., Ltd`` strip only as
      independent trailing tokens (``(?<![A-Za-z0-9])…$``), so ``Zinc`` / ``Prince``
      / ``Pre`` stay distinct.
    Empty residue after stripping is not a key.
    """
    text = (name or "").strip()
    if not text:
        return None
    previous = None
    while previous != text:
        previous = text
        text = _CORP_SUFFIX_EN_RE.sub("", text).strip(" -_/·•")
        folded = _fold(text)
        folded = _CORP_SUFFIX_KO_RE.sub("", folded)
        folded = _CORP_PREFIX_KO_RE.sub("", folded)
        text = folded.strip(" -_/·•")
    return text.casefold() or None


def _has_edge_corp_form_marker(name: str) -> bool:
    raw = name.strip()
    if not raw:
        return False
    if _CORP_SUFFIX_EN_RE.search(raw):
        return True
    folded = _fold(raw)
    return bool(
        _CORP_SUFFIX_KO_RE.search(folded) or _CORP_PREFIX_KO_RE.search(folded)
    )


def _prefer_company_display(a: str | None, b: str | None) -> str | None:
    sa = (a or "").strip() or None
    sb = (b or "").strip() or None
    if sa is None:
        return sb
    if sb is None:
        return sa
    # Prefer a form that still carries an explicit corporate marker.
    a_marked = _has_edge_corp_form_marker(sa)
    b_marked = _has_edge_corp_form_marker(sb)
    if a_marked and not b_marked:
        return sa
    if b_marked and not a_marked:
        return sb
    return sa if len(sa) >= len(sb) else sb


def _date_for_compat(value: str | None) -> str | None:
    """Normalize calendar tokens for dates_compatible (dash-separated)."""
    token = _normalize_date_token(value)
    if not token:
        return None
    if is_ongoing_end_marker(token):
        return token
    return token.replace(".", "-").replace("/", "-")


def _resolve_employment_end_merge(a: str | None, b: str | None) -> str | None:
    """Merge ends without choosing a conflict winner (REVIEW token on clash)."""
    if a in (None, "") and b in (None, ""):
        return None
    if a in (None, ""):
        return b
    if b in (None, ""):
        return a
    if a == b:
        return a
    if _EMPLOYMENT_END_CONFLICT_SEP in a or _EMPLOYMENT_END_CONFLICT_SEP in b:
        return _employment_end_conflict_token(a, b)
    if is_ongoing_end_marker(a) and is_ongoing_end_marker(b):
        return a
    if is_ongoing_end_marker(a) or is_ongoing_end_marker(b):
        return _employment_end_conflict_token(a, b)
    if dates_compatible(_date_for_compat(a), _date_for_compat(b)):
        return _prefer_date(a, b)
    return _employment_end_conflict_token(a, b)


@dataclass(frozen=True)
class _EmploymentEvidenceHit:
    """One original employment-section row that clearly matches company+period."""

    doc_id: str
    page_no: int
    folded_line: str
    quote_text: str
    start: str | None
    end: str | None

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.doc_id, self.page_no, self.folded_line)


def _employment_period_compatible(
    cand_start: str | None,
    cand_end: str | None,
    span_start: str | None,
    span_end: str | None,
) -> bool:
    """True when candidate period agrees with an original row (precision OK)."""
    if not span_start:
        return False
    if not dates_compatible(
        _date_for_compat(cand_start), _date_for_compat(span_start)
    ):
        return False
    if not cand_end or not span_end:
        return True
    if _is_ongoing_end(cand_end) or _is_ongoing_end(span_end):
        return _is_ongoing_end(cand_end) and _is_ongoing_end(span_end)
    return dates_compatible(
        _date_for_compat(cand_end), _date_for_compat(span_end)
    )


def _hits_share_single_period(hits: list[_EmploymentEvidenceHit]) -> bool:
    """False when hits cover mutually incompatible employer periods."""
    for index, left in enumerate(hits):
        for right in hits[index + 1 :]:
            if not dates_compatible(
                _date_for_compat(left.start), _date_for_compat(right.start)
            ):
                return False
            if not left.end or not right.end:
                continue
            if _is_ongoing_end(left.end) or _is_ongoing_end(right.end):
                if not (
                    _is_ongoing_end(left.end) and _is_ongoing_end(right.end)
                ):
                    return False
                continue
            if not dates_compatible(
                _date_for_compat(left.end), _date_for_compat(right.end)
            ):
                return False
    return True


def _is_employment_header_cell(line: str) -> bool:
    return bool(_EMPLOYMENT_HEADER_CELL_RE.match(line.strip()))


def _line_is_single_date_range(
    line: str,
) -> tuple[str | None, str | None] | None:
    """Return (start, end) when the cell is exactly one employment date range."""
    text = line.strip()
    if not text:
        return None
    matches = list(_DATE_RANGE_RE.finditer(text))
    if len(matches) != 1:
        return None
    residual = _DATE_RANGE_RE.sub("", text)
    residual = re.sub(r"[\s./\-~\—–·•|/]+", "", residual)
    if residual:
        return None
    match = matches[0]
    return (
        _normalize_date_token(match.group("start")),
        _normalize_date_token(match.group("end")),
    )


def _line_is_company_name_only_cell(line: str) -> bool:
    """True for a DOCX company-name cell (no dates / headers / section labels)."""
    text = line.strip()
    if not text or _is_employment_header_cell(text):
        return False
    if _DATE_TOKEN_RE.search(text) or _DATE_RANGE_RE.search(text):
        return False
    if _PROJECT_SECTION_RE.search(text) or _EDUCATION_SECTION_RE.search(text):
        return False
    if _CERT_SECTION_RE.search(text):
        return False
    if not re.search(r"[A-Za-z가-힣]", text):
        return False
    return len(text) <= 80


def _span_matches_employment_company(
    span_text: str, *, raw: str, stem: str | None
) -> bool:
    return bool(
        _line_has_company(span_text, raw)
        or (stem and _line_has_company(span_text, stem))
    )


def _employment_hit_from_quote(
    *,
    doc_id: str,
    page_no: int,
    quote_text: str,
    start: str | None,
    end: str | None,
) -> _EmploymentEvidenceHit:
    quote = quote_text.strip()
    if len(quote) > 500:
        quote = quote[:500]
    return _EmploymentEvidenceHit(
        doc_id=doc_id,
        page_no=page_no,
        folded_line=_fold(quote),
        quote_text=quote,
        start=start,
        end=end,
    )


def _clear_employment_evidence_hits(
    spans: list[_ContextSpan],
    *,
    company: str,
    start: str | None,
    end: str | None,
) -> list[_EmploymentEvidenceHit] | None:
    """Period-clear original rows for this employer.

    Accepts:
    - same-line company + date range (KOSA-style), or
    - company-name cell whose **immediate next original span** is also
      employment and exactly one date-range cell on the same document/page
      (DOCX table cells). Intervening project/other/ambiguous spans block
      pairing (do not skip via employment-only reindexing).

    Ignores possibly wrong ``source_refs``. Returns ``None`` when a line or the
    hit set is ambiguous (multiple incompatible periods). Empty list means no
    clear row. Never borrows project/education/other-company dates.
    """
    raw = (company or "").strip()
    if not raw or not (start or "").strip():
        return []
    stem = _company_match_key(raw)
    hits: list[_EmploymentEvidenceHit] = []
    # Walk original span order — never skip project/other/ambiguous neighbors
    # by filtering to employment-only indices first.
    for index, span in enumerate(spans):
        span_kind = _classify_span(span.text, section=span.section)
        if span_kind != "employment":
            continue
        if not _span_matches_employment_company(span.text, raw=raw, stem=stem):
            continue
        ranges = _extract_range_dates(span.text)
        if ranges:
            matched = [
                (span_start, span_end)
                for span_start, span_end in ranges
                if _employment_period_compatible(
                    start, end, span_start, span_end
                )
            ]
            if not matched:
                continue
            if len(matched) > 1:
                return None
            span_start, span_end = matched[0]
            hits.append(
                _employment_hit_from_quote(
                    doc_id=span.doc_id,
                    page_no=span.page_no,
                    quote_text=span.text,
                    start=span_start,
                    end=span_end,
                )
            )
            continue

        # DOCX: company cell → immediate next original span only, when that
        # neighbor is also employment and exactly one date-range cell.
        if not _line_is_company_name_only_cell(span.text):
            continue
        if index + 1 >= len(spans):
            continue
        nxt = spans[index + 1]
        if nxt.doc_id != span.doc_id or nxt.page_no != span.page_no:
            continue
        nxt_kind = _classify_span(nxt.text, section=nxt.section)
        if nxt_kind != "employment":
            continue
        range_pair = _line_is_single_date_range(nxt.text)
        if range_pair is None:
            continue
        span_start, span_end = range_pair
        if not _employment_period_compatible(start, end, span_start, span_end):
            continue
        quote = f"{span.text.strip()}\n{nxt.text.strip()}"
        hits.append(
            _employment_hit_from_quote(
                doc_id=span.doc_id,
                page_no=span.page_no,
                quote_text=quote,
                start=span_start,
                end=span_end,
            )
        )
    if not hits:
        return []
    if not _hits_share_single_period(hits):
        return None
    # Deduplicate identical keys while preserving first quote/period.
    unique: list[_EmploymentEvidenceHit] = []
    seen: set[tuple[str, int, str]] = set()
    for hit in hits:
        if hit.key in seen:
            continue
        seen.add(hit.key)
        unique.append(hit)
    return unique


def _employment_evidence_key_set(
    hits: list[_EmploymentEvidenceHit],
) -> set[tuple[str, int, str]]:
    return {hit.key for hit in hits}


def _has_independent_employment_evidence(
    left: EmploymentCandidate,
    right: EmploymentCandidate,
    *,
    spans: list[_ContextSpan],
) -> bool:
    """True when company+period has clear, distinct original-row evidence.

    Mislinked ``source_refs`` do not block discovery of a clear KOSA/DOCX row.
    A single shared original line never counts as dual evidence. Multiple
    incompatible period matches refuse the merge.
    """
    left_hits = _clear_employment_evidence_hits(
        spans,
        company=left.company_name or "",
        start=left.start_date,
        end=left.end_date,
    )
    right_hits = _clear_employment_evidence_hits(
        spans,
        company=right.company_name or "",
        start=right.start_date,
        end=right.end_date,
    )
    if left_hits is None or right_hits is None:
        return False
    if not left_hits or not right_hits:
        return False
    left_keys = _employment_evidence_key_set(left_hits)
    right_keys = _employment_evidence_key_set(right_hits)
    shared = left_keys & right_keys
    # Same employer period evidenced on ≥2 distinct original rows.
    if len(shared) >= 2:
        return True
    left_unique = left_keys - right_keys
    right_unique = right_keys - left_keys
    return bool(left_unique and right_unique)


def _verified_employment_source_refs(
    left: EmploymentCandidate,
    right: EmploymentCandidate,
    *,
    spans: list[_ContextSpan],
) -> list[SourceRef]:
    """Build refs only from clear original rows (never invent anchors)."""
    left_hits = _clear_employment_evidence_hits(
        spans,
        company=left.company_name or "",
        start=left.start_date,
        end=left.end_date,
    ) or []
    right_hits = _clear_employment_evidence_hits(
        spans,
        company=right.company_name or "",
        start=right.start_date,
        end=right.end_date,
    ) or []
    refs: list[SourceRef] = []
    seen: set[tuple[str, int, str]] = set()
    for hit in [*left_hits, *right_hits]:
        if hit.key in seen:
            continue
        seen.add(hit.key)
        refs.append(
            SourceRef(
                document_id=hit.doc_id,
                page_no=hit.page_no,
                quote_text=hit.quote_text,
            )
        )
    return refs


def _merge_employment_duplicate_pair(
    left: EmploymentCandidate,
    right: EmploymentCandidate,
    *,
    spans: list[_ContextSpan],
) -> EmploymentCandidate:
    verified = _verified_employment_source_refs(left, right, spans=spans)
    return left.model_copy(
        update={
            "company_name": _prefer_company_display(
                left.company_name, right.company_name
            ),
            "department": left.department or right.department,
            "title": left.title or right.title,
            "start_date": _prefer_date(left.start_date, right.start_date),
            "end_date": _resolve_employment_end_merge(
                left.end_date, right.end_date
            ),
            "responsibilities": left.responsibilities or right.responsibilities,
            "confidence": max(left.confidence or 0.0, right.confidence or 0.0)
            or left.confidence
            or right.confidence,
            "source_refs": _merge_source_refs(
                _merge_source_refs(left.source_refs, right.source_refs),
                verified,
            ),
        }
    )


def _employment_duplicates_mergeable(
    left: EmploymentCandidate,
    right: EmploymentCandidate,
    *,
    spans: list[_ContextSpan],
) -> bool:
    """Same employer period with independent page evidence; no fuzzy names."""
    left_key = _company_match_key(left.company_name)
    right_key = _company_match_key(right.company_name)
    # Empty keys (e.g. bare ``(주)``) must never merge.
    if not left_key or not right_key or left_key != right_key:
        return False
    left_start = (left.start_date or "").strip() or None
    right_start = (right.start_date or "").strip() or None
    # Distinct undated periods must not collapse.
    if not left_start or not right_start:
        return False
    if not dates_compatible(
        _date_for_compat(left_start), _date_for_compat(right_start)
    ):
        return False
    # Ambiguous / shared-only original lines do not count as dual evidence.
    if not _has_independent_employment_evidence(left, right, spans=spans):
        return False
    return True


def _dedupe_employment_history(
    rows: list[EmploymentCandidate],
    *,
    spans: list[_ContextSpan],
) -> list[EmploymentCandidate]:
    """Merge DOCX/KOSA employer duplicates; keep conflicting ends as REVIEW."""
    merged: list[EmploymentCandidate] = []
    for row in rows:
        placed = False
        for index, prev in enumerate(merged):
            if not _employment_duplicates_mergeable(prev, row, spans=spans):
                continue
            merged[index] = _merge_employment_duplicate_pair(
                prev, row, spans=spans
            )
            placed = True
            break
        if not placed:
            merged.append(row)
    return merged


def _guard_employment_history(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    document_type_by_id: dict[str, str] | None,
) -> list[EmploymentCandidate]:
    """Drop certain project-customer mix-ins; REVIEW start-linked end conflicts.

    Uses document/page/section spans (including collapsed KOSA lines). Project
    page dates never count as employer-table evidence. Ambiguous rows are kept.
    After customer filtering, merge clear DOCX/KOSA employer duplicates when
    company form + compatible start + employment-section evidence agree.
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
        contiguous = _try_resolve_kosa_contiguous_employment_end(
            company=company,
            candidate_start=row.start_date,
            candidate_end=row.end_date,
            linked_ends=linked_ends,
            spans=spans,
        )
        if contiguous is not None:
            final_end, verified_refs = contiguous
            updated = row.model_copy(
                update={
                    "end_date": final_end,
                    "source_refs": _merge_source_refs(
                        row.source_refs, verified_refs
                    ),
                }
            )
        else:
            review_end = _resolve_end_date_for_review(
                candidate_end=row.end_date, linked_ends=linked_ends
            )
            if review_end is not None:
                updated = row.model_copy(update={"end_date": review_end})
        kept.append(updated)

    return _dedupe_employment_history(kept, spans=spans)


def _optional_text_compatible(a: str | None, b: str | None) -> bool:
    ka = _key(a)
    kb = _key(b)
    if ka is None or kb is None:
        return True
    return ka == kb


def _prefer_text(a: str | None, b: str | None) -> str | None:
    sa = (a or "").strip() or None
    sb = (b or "").strip() or None
    if sa is None:
        return sb
    if sb is None:
        return sa
    return sa if len(sa) >= len(sb) else sb


def _prefer_date(a: str | None, b: str | None) -> str | None:
    na = (a or "").strip() or None
    nb = (b or "").strip() or None
    if na is None:
        return nb
    if nb is None:
        return na
    if na == nb:
        return na
    score_a = sum(ch.isdigit() for ch in na) + na.count("-") + na.count(".")
    score_b = sum(ch.isdigit() for ch in nb) + nb.count("-") + nb.count(".")
    return nb if score_b > score_a else na


def _merge_source_refs(
    left: list[SourceRef], right: list[SourceRef]
) -> list[SourceRef]:
    seen: set[tuple[str, int | None, str]] = set()
    out: list[SourceRef] = []
    for ref in [*(left or []), *(right or [])]:
        key = (
            (ref.document_id or "").strip(),
            ref.page_no,
            (ref.quote_text or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _education_rows_compatible(
    left: EducationCandidate, right: EducationCandidate
) -> bool:
    if not _key(left.school_name) or not _key(right.school_name):
        return False
    if _key(left.school_name) != _key(right.school_name):
        return False
    if not _optional_text_compatible(left.degree, right.degree):
        return False
    if not _optional_text_compatible(left.major, right.major):
        return False
    if not dates_compatible(left.start_date, right.start_date):
        return False
    if not dates_compatible(left.end_date, right.end_date):
        return False
    return True


def _merge_education_pair(
    left: EducationCandidate, right: EducationCandidate
) -> EducationCandidate:
    return left.model_copy(
        update={
            "major": left.major or right.major,
            "degree": left.degree or right.degree,
            "start_date": _prefer_date(left.start_date, right.start_date),
            "end_date": _prefer_date(left.end_date, right.end_date),
            "status": left.status or right.status,
            "confidence": max(left.confidence or 0.0, right.confidence or 0.0)
            or left.confidence
            or right.confidence,
            "source_refs": _merge_source_refs(left.source_refs, right.source_refs),
        }
    )


_MAJOR_REJECT_RE = re.compile(
    r"자격증|자격면허|발급기관|근무경력|재직|프로젝트|수행내역|고객사|"
    r"기사$|산업인력|산업입력|회사|직장|발주"
)


def _split_edu_cert_collapsed(
    line: str, *, page_section: str
) -> list[tuple[str, str]]:
    """Split KOSA-style mixed lines into edu/cert/other spans."""
    raw: list[tuple[int, int, str]] = []
    for match in _EDUCATION_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "education"))
    for match in _CERT_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "certification"))
    for match in _EMPLOYMENT_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "other"))
    for match in _PROJECT_SECTION_RE.finditer(line):
        raw.append((match.start(), match.end(), "other"))
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


def _iter_edu_cert_section_lines(
    page_texts: dict[tuple[str, int], str] | None,
) -> Iterable[tuple[str, str]]:
    """Yield (section, line); sticky never crosses document boundaries."""
    if not page_texts:
        return
    sticky = "unknown"
    prev_doc: str | None = None
    ordered = sorted(
        page_texts.items(),
        key=lambda item: (str(item[0][0]), int(item[0][1])),
    )
    for (doc_id, _page_no), text in ordered:
        doc_key = str(doc_id)
        if prev_doc != doc_key:
            sticky = "unknown"
            prev_doc = doc_key
        for raw_line in str(text or "").splitlines() or [str(text or "")]:
            line = raw_line.strip()
            if not line:
                continue
            for section_hint, span_text in _split_edu_cert_collapsed(
                line, page_section=sticky
            ):
                if _EDUCATION_SECTION_RE.search(span_text):
                    sticky = "education"
                    section = "education"
                elif _CERT_SECTION_RE.search(span_text):
                    sticky = "certification"
                    section = "certification"
                elif (
                    _EMPLOYMENT_SECTION_RE.search(span_text)
                    or _PROJECT_SECTION_RE.search(span_text)
                ):
                    sticky = "other"
                    section = "other"
                else:
                    section = (
                        section_hint if section_hint != "unknown" else sticky
                    )
                yield section, span_text


def _looks_like_major(value: str) -> bool:
    text = " ".join(value.split()).strip(" -/~\t|/·•")
    if not text or len(text) > 30:
        return False
    if _DATE_TOKEN_RE.search(text) or _MAJOR_REJECT_RE.search(text):
        return False
    if len(text.split()) > 4:
        return False
    return True


def _extract_education_fields_from_line(
    line: str, *, school: str, allow_major: bool
) -> dict[str, str]:
    """Pull major/dates/degree only from a line that already contains school."""
    if not _line_has_company(line, school):
        return {}
    out: dict[str, str] = {}
    ranges = _extract_range_dates(line)
    if ranges:
        start, end = ranges[0]
        if start:
            out["start_date"] = start
        if end and not _is_ongoing_end(end):
            out["end_date"] = end
    # Degree tokens commonly appear as short trailing labels.
    for degree in ("학사", "석사", "박사", "전문학사", "고졸"):
        if degree in line:
            out["degree"] = degree
            break
    if not allow_major:
        return out
    # Major: token between school and first date, or after school before degree.
    school_match = re.search(re.escape(school.strip()), line)
    if school_match:
        after = line[school_match.end() :]
        after = _DATE_RANGE_RE.sub(" ", after)
        after = _DATE_TOKEN_RE.sub(" ", after)
        for degree in ("학사", "석사", "박사", "전문학사", "고졸"):
            after = after.replace(degree, " ")
        after = re.sub(r"[|/·•,，]+", " ", after)
        major = " ".join(after.split()).strip(" -/~\t")
        if _looks_like_major(major):
            out["major"] = major
    return out


def _enrich_education_from_pages(
    row: EducationCandidate,
    page_texts: dict[tuple[str, int], str] | None,
) -> EducationCandidate:
    """Fill missing education fields only from clearly linked table lines."""
    school = (row.school_name or "").strip()
    if not school or not page_texts:
        return row
    updates: dict[str, object] = {}
    for section, line in _iter_edu_cert_section_lines(page_texts):
        if section in {"certification", "other"}:
            continue
        if not _line_has_company(line, school):
            continue
        # Major only from explicit education spans — never absorb cert/other
        # table residue from unknown/mixed regions.
        allow_major = section == "education"
        if section != "education" and not (
            _EDUCATION_SECTION_RE.search(line) or _DATE_RANGE_RE.search(line)
        ):
            continue
        extracted = _extract_education_fields_from_line(
            line, school=school, allow_major=allow_major
        )
        for field in ("major", "degree", "start_date", "end_date"):
            current = getattr(row, field)
            incoming = extracted.get(field)
            if not incoming:
                continue
            if current is None or not str(current).strip():
                updates[field] = incoming
            elif field in {"start_date", "end_date"}:
                if not dates_compatible(current, incoming):
                    # Conflict → leave original; REVIEW happens later in diff.
                    continue
                updates[field] = _prefer_date(str(current), incoming)
            elif _key(str(current)) != _key(incoming):
                continue
        if updates:
            break
    if not updates:
        return row
    return row.model_copy(update=updates)


def _guard_education(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
) -> list[EducationCandidate]:
    rows = list(candidate.education or [])
    if not rows:
        return rows
    # Deduplicate clear same-school rows (missing start alone must not block).
    merged: list[EducationCandidate] = []
    for row in rows:
        if not _key(row.school_name):
            merged.append(row)
            continue
        placed = False
        for index, prev in enumerate(merged):
            if not _education_rows_compatible(prev, row):
                continue
            merged[index] = _merge_education_pair(prev, row)
            placed = True
            break
        if not placed:
            merged.append(row)
    return [
        _enrich_education_from_pages(row, page_texts) for row in merged
    ]


def _cert_ocr_canonical_name(name: str) -> str:
    text = (name or "").strip()
    for bad, good in _CERT_OCR_CANONICAL:
        if _key(text) == _key(bad):
            return good
        text = text.replace(bad, good)
    return text


def _cert_match_key(name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    return _key(_cert_ocr_canonical_name(name))


def _is_known_ocr_cert_pair(a: str | None, b: str | None) -> bool:
    ka = _cert_match_key(a)
    kb = _cert_match_key(b)
    if ka is None or kb is None or ka != kb:
        return False
    return _key(a) != _key(b)


def _line_has_compatible_date(line: str, acquired: str | None) -> bool:
    if not (acquired or "").strip():
        return False
    target = _normalize_date_token(acquired)
    if not target:
        return False
    for match in _DATE_TOKEN_RE.finditer(line):
        token = _normalize_date_token(match.group(0))
        if token and dates_compatible(target, token):
            return True
    return False


def _cert_literal_name_in_line(line: str, name: str | None) -> bool:
    """Literal spelling only — OCR aliases are never used for page evidence."""
    if not name or not name.strip():
        return False
    return _line_has_company(line, name.strip())


def _cert_name_bound_to_acquired_on_line(
    line: str, *, name: str, acquired: str
) -> bool:
    """True when literal name owns a compatible acquired date on this line.

    Collapsed KOSA rows often pack ``NameA DateA NameB DateB`` into one line.
    Each date is bound only to the text segment since the previous date, so
    NameA cannot claim DateB.
    """
    target = _normalize_date_token(acquired)
    if not target or not name.strip():
        return False
    date_matches = list(_DATE_TOKEN_RE.finditer(line))
    if not date_matches:
        return False
    prev_end = 0
    bound_hits = 0
    for match in date_matches:
        prefix = line[prev_end : match.start()]
        token = _normalize_date_token(match.group(0))
        prev_end = match.end()
        if not token or not dates_compatible(target, token):
            continue
        if _cert_literal_name_in_line(prefix, name):
            bound_hits += 1
    # Ambiguous: same name appears with multiple distinct owning dates.
    return bound_hits == 1


def _line_is_cert_header_only(line: str) -> bool:
    return bool(_CERT_HEADER_ONLY_RE.match(line.strip()))


def _line_is_single_date(line: str) -> str | None:
    """Return normalized date when the line is exactly one date token."""
    text = line.strip()
    matches = list(_DATE_TOKEN_RE.finditer(text))
    if len(matches) != 1:
        return None
    residual = _DATE_TOKEN_RE.sub("", text)
    residual = re.sub(r"[\s./\-~\—–·•|/]+", "", residual)
    if residual:
        return None
    return _normalize_date_token(matches[0].group(0))


def _line_is_cert_name_only(line: str) -> bool:
    """True for a cert-title-only cell (no dates / section headers)."""
    text = line.strip()
    if not text or _line_is_cert_header_only(text):
        return False
    if _DATE_TOKEN_RE.search(text) or _DATE_RANGE_RE.search(text):
        return False
    if not _CERT_NAME_SHAPE_RE.search(text):
        return False
    return len(text) <= 40


def _pair_local_adjacent_cert_cells(
    cells: list[str],
) -> list[tuple[str, str]]:
    """Collect clear Name→next-Date adjacent pairs inside a mixed cell block.

    Only the immediate next cell may supply the acquired date. A date whose
    previous cell is not a cert-title cell is ignored (no neighbor/prev-cert
    borrowing, no education-date attachment).
    """
    pairs: list[tuple[str, str]] = []
    for index in range(len(cells) - 1):
        name_cell = cells[index]
        date_token = _line_is_single_date(cells[index + 1])
        if not date_token or not _line_is_cert_name_only(name_cell):
            continue
        pairs.append((name_cell, date_token))
    return pairs


def _pair_cert_names_and_dates_in_block(
    lines: list[str],
) -> list[tuple[str, str]] | None:
    """Pair cert-name cells to acquired dates inside one certification block.

    Recognition order:
    - interleaved: Name, Date, Name, Date, ... (entire block)
    - columnar: Name×N then Date×N (entire block)
    - local adjacent: Name cell immediately followed by a date cell, even when
      the surrounding DOCX certification table mixes other values
    Returns None when no unambiguous pairs exist.
    """
    cells = [
        line.strip()
        for line in lines
        if line.strip() and not _line_is_cert_header_only(line)
    ]
    if not cells:
        return None

    # Interleaved Name/Date pairs.
    interleaved: list[tuple[str, str]] = []
    index = 0
    interleaved_ok = True
    while index < len(cells):
        if index + 1 >= len(cells):
            interleaved_ok = False
            break
        name_cell = cells[index]
        date_cell = cells[index + 1]
        date_token = _line_is_single_date(date_cell)
        if not (_line_is_cert_name_only(name_cell) and date_token):
            interleaved_ok = False
            break
        interleaved.append((name_cell, date_token))
        index += 2
    if interleaved_ok and interleaved and index == len(cells):
        return interleaved

    # Columnar dump: all names, then the same number of dates.
    names = [cell for cell in cells if _line_is_cert_name_only(cell)]
    dates = [_line_is_single_date(cell) for cell in cells]
    date_tokens = [token for token in dates if token is not None]
    if (
        names
        and len(names) == len(date_tokens)
        and len(names) + len(date_tokens) == len(cells)
        and all(_line_is_cert_name_only(cell) for cell in cells[: len(names)])
        and all(
            _line_is_single_date(cell) for cell in cells[len(names) :]
        )
    ):
        return list(zip(names, date_tokens, strict=True))

    local = _pair_local_adjacent_cert_cells(cells)
    return local or None


def _cert_name_bound_in_cert_block(
    lines: list[str], *, name: str, acquired: str
) -> bool:
    """True when block pairing uniquely binds literal name to acquired date."""
    target = _normalize_date_token(acquired)
    if not target or not name.strip():
        return False
    pairs = _pair_cert_names_and_dates_in_block(lines)
    if not pairs:
        return False
    hits = 0
    for pair_name, pair_date in pairs:
        if not _cert_literal_name_in_line(pair_name, name):
            continue
        if dates_compatible(target, pair_date):
            hits += 1
    return hits == 1


def _iter_certification_blocks(
    page_texts: dict[tuple[str, int], str] | None,
) -> Iterable[list[str]]:
    """Yield certification-section line blocks; never join across documents."""
    if not page_texts:
        return
    by_doc: dict[str, dict[tuple[str, int], str]] = {}
    for (doc_id, page_no), text in page_texts.items():
        by_doc.setdefault(str(doc_id), {})[(str(doc_id), int(page_no))] = text
    for doc_key in sorted(by_doc):
        block: list[str] = []
        for section, line in _iter_edu_cert_section_lines(by_doc[doc_key]):
            if section == "certification":
                block.append(line)
                continue
            if block:
                yield block
                block = []
        if block:
            yield block


def _cert_row_evidence(
    page_texts: dict[tuple[str, int], str] | None,
    *,
    name: str | None,
    acquired: str | None,
) -> bool:
    """True when source text binds this exact name to its acquired date.

    Prefer same-line ownership (KOSA collapsed rows). For DOCX mixed tables,
    certification-section cells may place names and dates on separate lines;
    those are paired only via unambiguous interleaved/columnar block shape.
    Education admission/graduation dates are never paired across lines.
    """
    if not page_texts or not name or not acquired:
        return False

    for block in _iter_certification_blocks(page_texts):
        for line in block:
            if _cert_name_bound_to_acquired_on_line(
                line, name=name, acquired=acquired
            ):
                return True
        if _cert_name_bound_in_cert_block(
            block, name=name, acquired=acquired
        ):
            return True

    # Same-line only outside certification sticky: cert rows sometimes remain
    # under education/unknown when a 자격증 header was not extracted. Never
    # multi-line pair here (avoids school period dates).
    if not _CERT_NAME_SHAPE_RE.search(name):
        return False
    for section, line in _iter_edu_cert_section_lines(page_texts):
        if section in {"certification", "other"}:
            continue
        if _cert_name_bound_to_acquired_on_line(
            line, name=name, acquired=acquired
        ):
            return True
    return False


def _issuer_on_cert_row(
    page_texts: dict[tuple[str, int], str] | None,
    *,
    name: str | None,
    issuer: str | None,
) -> bool:
    """Issuer must share a cert-context line with the literal certification name."""
    if not page_texts or not name or not issuer:
        return False
    for section, line in _iter_edu_cert_section_lines(page_texts):
        if section == "education":
            continue
        if not _cert_literal_name_in_line(line, name):
            continue
        if not _line_has_company(line, issuer.strip()):
            continue
        if section == "certification" or _CERT_SECTION_RE.search(line):
            return True
        # Name+issuer+date on one line without a header still counts.
        if _DATE_TOKEN_RE.search(line):
            return True
    return False


def _prefer_cert_name(a: str | None, b: str | None) -> str | None:
    sa = (a or "").strip() or None
    sb = (b or "").strip() or None
    if sa is None:
        return sb
    if sb is None:
        return sa
    canon_a = _cert_ocr_canonical_name(sa)
    canon_b = _cert_ocr_canonical_name(sb)
    # Prefer already-canonical Hangul spelling from DOCX over OCR corruption.
    if canon_a == sa and canon_b != sb:
        return sa
    if canon_b == sb and canon_a != sa:
        return sb
    return _prefer_text(sa, sb)


def _merge_cert_pair(
    left: CertificationCandidate, right: CertificationCandidate
) -> CertificationCandidate:
    name = _prefer_cert_name(left.certification_name, right.certification_name)
    # Do not invent issuers; prefer a non-empty value only when both agree or
    # one side is empty. Conflicting issuers are cleared for REVIEW safety.
    issuer_l = (left.issuer or "").strip() or None
    issuer_r = (right.issuer or "").strip() or None
    if issuer_l and issuer_r and _key(issuer_l) != _key(issuer_r):
        issuer: str | None = None
    else:
        issuer = issuer_l or issuer_r
    return left.model_copy(
        update={
            "certification_name": name,
            "issuer": issuer,
            "acquired_date": _prefer_date(left.acquired_date, right.acquired_date),
            "expiry_date": _prefer_date(left.expiry_date, right.expiry_date),
            "confidence": max(left.confidence or 0.0, right.confidence or 0.0)
            or left.confidence
            or right.confidence,
            "source_refs": _merge_source_refs(left.source_refs, right.source_refs),
        }
    )


def _certs_mergeable(
    left: CertificationCandidate,
    right: CertificationCandidate,
    *,
    page_texts: dict[tuple[str, int], str] | None,
) -> bool:
    """Merge only with dated rows + exact/OCR name rules + page evidence.

    OCR aliases are used only to compare candidate names. Page evidence for
    each side requires that side's literal spelling and acquired date on the
    same source row. Undated certifications never auto-merge.
    """
    left_date = (left.acquired_date or "").strip() or None
    right_date = (right.acquired_date or "").strip() or None
    if not left_date or not right_date:
        return False
    if _cert_match_key(left.certification_name) != _cert_match_key(
        right.certification_name
    ):
        return False
    if not dates_compatible(
        _normalize_date_token(left_date),
        _normalize_date_token(right_date),
    ):
        return False
    left_ev = _cert_row_evidence(
        page_texts,
        name=left.certification_name,
        acquired=left_date,
    )
    right_ev = _cert_row_evidence(
        page_texts,
        name=right.certification_name,
        acquired=right_date,
    )
    if _key(left.certification_name) == _key(right.certification_name):
        # Exact name + both dated: merge when evidenced, or when pages are
        # unavailable (issuer-only duplicates inside one extraction).
        if page_texts is None:
            return True
        return left_ev or right_ev
    if not _is_known_ocr_cert_pair(
        left.certification_name, right.certification_name
    ):
        return False
    # OCR pair: each spelling must appear with its date independently.
    return left_ev and right_ev


def _guard_certifications(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
) -> list[CertificationCandidate]:
    rows = list(candidate.certifications or [])
    if not rows:
        return rows
    school_keys = {
        key
        for edu in candidate.education or []
        if (key := _key(edu.school_name)) is not None
    }
    merged: list[CertificationCandidate] = []
    for row in rows:
        placed = False
        for index, prev in enumerate(merged):
            if not _certs_mergeable(prev, row, page_texts=page_texts):
                continue
            merged[index] = _merge_cert_pair(prev, row)
            placed = True
            break
        if not placed:
            merged.append(row)

    cleaned: list[CertificationCandidate] = []
    for row in merged:
        issuer = (row.issuer or "").strip() or None
        if issuer and _key(issuer) in school_keys:
            if not _issuer_on_cert_row(
                page_texts,
                name=row.certification_name,
                issuer=issuer,
            ):
                issuer = None
        # Never auto-generate standard issuers (e.g. 한국산업인력공단).
        cleaned.append(
            row
            if issuer == ((row.issuer or "").strip() or None)
            else row.model_copy(update={"issuer": issuer})
        )
    return cleaned


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
    kept_education = _guard_education(candidate, page_texts=page_texts)
    # Cert guard may clear school-polluted issuers using education school names
    # from the post-dedup education list.
    cert_input = candidate.model_copy(update={"education": kept_education})
    kept_certifications = _guard_certifications(
        cert_input, page_texts=page_texts
    )

    if (
        profile is candidate.profile
        and len(kept_projects) == len(candidate.projects)
        and kept_employment == list(candidate.employment_history or [])
        and kept_education == list(candidate.education or [])
        and kept_certifications == list(candidate.certifications or [])
    ):
        return candidate
    return candidate.model_copy(
        update={
            "profile": profile,
            "projects": kept_projects,
            "employment_history": kept_employment,
            "education": kept_education,
            "certifications": kept_certifications,
        }
    )
