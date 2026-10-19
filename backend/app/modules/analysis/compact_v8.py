"""v8 compact intermediate protocol adapters (aliases + expand → candidate-v1)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.ai.schemas.profile_candidate import (
    PROFILE_SCALAR_FIELD_NAMES,
    SCHEMA_VERSION,
    CodeRefCandidate,
    EducationCandidate,
    ExpertiseCandidate,
    JobCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SkillCandidate,
    SourceRef,
)

_DOCUMENT_BLOCK_RE = re.compile(
    r"\[DOCUMENT\]\n"
    r"document_id:\s*([^\n]+)\n"
    r"(filename:\s*[^\n]+\n)"
    r"(document_type:\s*[^\n]+\n)",
)

# Compact profile scalar keys → canonical field names.
_PROFILE_KEY_MAP: dict[str, str] = {
    "n": "name",
    "by": "birth_year",
    "ph": "phone",
    "em": "email",
    "ar": "address_region",
    "ac": "affiliation_company",
    "dp": "department",
    "ti": "current_title",
    "et": "employment_type",
    "tg": "technical_grade",
    "cs": "career_start_date",
    "cdv": "career_document_value",
    "ps": "profile_summary",
}

# Reverse for optional long-key passthrough in compact profile.
_PROFILE_LONG_KEYS = frozenset(PROFILE_SCALAR_FIELD_NAMES)

# Profile fields that are optional text (not birth_year).
_PROFILE_TEXT_FIELDS = frozenset(PROFILE_SCALAR_FIELD_NAMES) - {"birth_year"}


def _optional_text(value: Any) -> str | None:
    """Fail-soft optional canonical text: keep clean non-empty strings only.

    - str → strip; empty → omit
    - None / bool / list / dict / other non-str → omit
    Never stringifies malformed values (e.g. True → \"True\").
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return None


def _set_optional_text(out: dict[str, Any], key: str, value: Any) -> None:
    text = _optional_text(value)
    if text is not None:
        out[key] = text


def _optional_int(value: Any) -> int | None:
    """Fail-soft optional integer for compact numeric scalars.

    Accepts ``int`` (not bool) and strictly numeric integer strings.
    Rejects floats, bools, containers, and arbitrary text (e.g. ``D1``).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Strict integer string only (no floats, signs beyond optional +/-, aliases).
        if text[0] in "+-" and text[1:].isdigit():
            return int(text)
        if text.isdigit():
            return int(text)
        return None
    return None


def _optional_bool(value: Any) -> bool | None:
    """Fail-soft optional boolean for compact skill ``rep``.

    Accepts only real ``bool`` (``True`` / ``False``).
    Rejects str/int/float/list/dict/None and other non-bool values.
    """
    if isinstance(value, bool):
        return value
    return None


# Anchored birth-year date forms that start with YYYY (no mid-string inference).
_BIRTH_YEAR_DATE_RE = re.compile(
    r"^(\d{4})"
    r"(?:"
    r"년(?:\s*\d{1,2}월(?:\s*\d{1,2}일)?)?"
    r"|"
    r"[.\-/]\d{1,2}(?:[.\-/]\d{1,2})?"
    r")$"
)


def _optional_birth_year(value: Any) -> int | None:
    """Fail-soft birth_year for compact profile ``by``.

    Accepts:
    - ``int`` (not bool)
    - plain 4-digit numeric string (``\"1976\"``)
    - anchored full/partial date strings beginning with a 4-digit year
      (``\"1976년 04월 05일\"``, ``\"1976-04-05\"``, ``\"1976.04.05\"``,
      ``\"1976/04/05\"``, ``\"1976년\"``)

    Rejects floats, bools, containers, short years, trailing junk, and years
    that appear later in arbitrary text (e.g. ``\"생년 1976\"``).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 4:
        return int(text)
    match = _BIRTH_YEAR_DATE_RE.fullmatch(text)
    if match is None:
        return None
    return int(match.group(1))


_YM_RE = re.compile(r"^(\d{4})[.\-](\d{2})$")
_STRICT_RELATION_KEYS = frozenset({"j", "t", "x"})
_CUSTOMER_CODE_TYPES = frozenset({"BIZ", "CUSTOMER_TYPE"})


def parse_year_month(value: Any) -> tuple[int, int] | None:
    """Parse exact YYYY.MM / YYYY-MM; otherwise None."""
    text = _optional_text(value)
    if text is None:
        return None
    match = _YM_RE.match(text)
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        return None
    return year, month


def derive_duration_months(start: Any, end: Any) -> int | None:
    """Deterministic month delta for exact year-month dates.

    ``(end_year - start_year) * 12 + (end_month - start_month)``.
    Reversed / insufficient precision => None.
    """
    start_ym = parse_year_month(start)
    end_ym = parse_year_month(end)
    if start_ym is None or end_ym is None:
        return None
    start_year, start_month = start_ym
    end_year, end_month = end_ym
    delta = (end_year - start_year) * 12 + (end_month - start_month)
    if delta < 0:
        return None
    return delta


_PROJECT_DATE_RANGE_RE = re.compile(
    r"(?P<sy>\d{2}|\d{4})[.\-](?P<sm>\d{2})\s*[~～]\s*"
    r"(?P<ey>\d{2}|\d{4})[.\-](?P<em>\d{2})"
)
_FOUR_DIGIT_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _collect_year_anchors(*texts: str | None) -> list[int]:
    anchors: list[int] = []
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        for match in _FOUR_DIGIT_YEAR_RE.finditer(text):
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                anchors.append(year)
    return anchors


def _expand_year_token(
    token: str,
    *,
    anchors: list[int],
) -> int | None:
    if len(token) == 4:
        year = int(token)
        return year if 1900 <= year <= 2100 else None
    if len(token) != 2:
        return None
    yy = int(token)
    matched = sorted({year for year in anchors if year % 100 == yy})
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return None
    return 2000 + yy if yy <= 49 else 1900 + yy


def _format_year_month(year: int, month: int) -> str:
    return f"{year:04d}.{month:02d}"


def parse_project_date_range_from_texts(
    texts: list[str],
    *,
    project_name: str | None = None,
) -> tuple[str, str] | None:
    """Parse a unique YYYY.MM~YYYY.MM range from texts; else None (fail-closed)."""
    if not texts:
        return None
    anchors = _collect_year_anchors(project_name, *texts)
    parsed: set[tuple[str, str]] = set()
    for text in texts:
        for match in _PROJECT_DATE_RANGE_RE.finditer(text):
            sm = int(match.group("sm"))
            em = int(match.group("em"))
            if sm < 1 or sm > 12 or em < 1 or em > 12:
                return None
            sy_tok = match.group("sy")
            ey_tok = match.group("ey")
            start_year = _expand_year_token(sy_tok, anchors=anchors)
            end_year = _expand_year_token(ey_tok, anchors=anchors)
            if start_year is None or end_year is None:
                return None
            # Classic two-digit wrap: 99.12 ~ 00.02 under pivot already yields
            # 1999/2000. If end < start, try one century bump on YY end only.
            if (end_year, em) < (start_year, sm):
                if len(ey_tok) == 2:
                    bumped = end_year + 100
                    if bumped <= 2100 and (bumped, em) >= (start_year, sm):
                        end_year = bumped
                    else:
                        return None
                else:
                    return None
            parsed.add(
                (
                    _format_year_month(start_year, sm),
                    _format_year_month(end_year, em),
                )
            )
            if len(parsed) > 1:
                return None
    if len(parsed) != 1:
        return None
    return next(iter(parsed))


def backfill_project_dates_from_root_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: fill missing project start/end from unique root-quote date ranges.

    Uses only project root ``source_refs[].quote_text``. Never reads relation
    j/t/x refs or page text. Existing start/end values are never overwritten.
    Recalculates ``duration_months`` via ``derive_duration_months`` when both
    ends are present after fill.
    """
    if not candidate.projects:
        return candidate
    projects: list[ProjectCandidate] = []
    changed_any = False
    for project in candidate.projects:
        need_start = not (project.start_date or "").strip()
        need_end = not (project.end_date or "").strip()
        if not need_start and not need_end:
            projects.append(project)
            continue
        quotes = [
            (ref.quote_text or "").strip()
            for ref in (project.source_refs or [])
            if (ref.quote_text or "").strip()
        ]
        if not quotes:
            projects.append(project)
            continue
        parsed = parse_project_date_range_from_texts(
            quotes, project_name=project.project_name
        )
        if parsed is None:
            projects.append(project)
            continue
        start_s, end_s = parsed
        updates: dict[str, Any] = {}
        if need_start and need_end:
            updates["start_date"] = start_s
            updates["end_date"] = end_s
        elif need_start:
            if parse_year_month(project.end_date) == parse_year_month(end_s):
                updates["start_date"] = start_s
        elif need_end:
            if parse_year_month(project.start_date) == parse_year_month(start_s):
                updates["end_date"] = end_s
        if not updates:
            projects.append(project)
            continue
        new_start = updates.get("start_date", project.start_date)
        new_end = updates.get("end_date", project.end_date)
        duration = derive_duration_months(new_start, new_end)
        if duration is not None:
            updates["duration_months"] = duration
        projects.append(project.model_copy(update=updates))
        changed_any = True
    if not changed_any:
        return candidate
    return candidate.model_copy(update={"projects": projects})


def build_document_alias_view(document_blocks: str) -> tuple[str, dict[str, str]]:
    """Replace UUID document headers with D1/D2… aliases for the LLM prompt.

    Returns ``(aliased_blocks, alias_to_document_id)``.
    Does not mutate AnalysisSourceBuilder; UUID keys remain authoritative
    for allowed_documents / page_texts validation.
    """
    alias_to_id: dict[str, str] = {}
    counter = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal counter
        doc_id = match.group(1).strip()
        if not doc_id:
            return match.group(0)
        # Stable alias per distinct document_id within the run.
        for alias, mapped in alias_to_id.items():
            if mapped == doc_id:
                return f"[DOCUMENT {alias}]\n{match.group(2)}{match.group(3)}"
        counter += 1
        alias = f"D{counter}"
        alias_to_id[alias] = doc_id
        return f"[DOCUMENT {alias}]\n{match.group(2)}{match.group(3)}"

    aliased = _DOCUMENT_BLOCK_RE.sub(_repl, document_blocks or "")
    return aliased, alias_to_id


def _expand_ref(item: Any, alias_to_id: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    # Compact: {"d":"D1","p":1,"q":"..."} or already-canonical keys.
    alias = item.get("d")
    doc_id = item.get("document_id")
    if alias is not None:
        alias_key = _optional_text(alias)
        if alias_key is None:
            return None
        mapped = alias_to_id.get(alias_key)
        if mapped is None:
            return None  # unknown alias — drop safely
        doc_id = mapped
    else:
        doc_id = _optional_text(doc_id)
    if doc_id is None:
        return None
    # Fail-soft: keep ref when page_no is malformed; omit only the field.
    # Accept positive ints / numeric strings via p or canonical page_no.
    raw_page = item.get("p", item.get("page_no"))
    page_no = _optional_int(raw_page)
    quote = _optional_text(item.get("q", item.get("quote_text")))
    out: dict[str, Any] = {"document_id": doc_id}
    if page_no is not None and page_no > 0:
        out["page_no"] = page_no
    if quote is not None:
        out["quote_text"] = quote
    return out


def expand_compact_refs(
    refs: Any, alias_to_id: dict[str, str]
) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    out: list[dict[str, Any]] = []
    for item in refs:
        expanded = _expand_ref(item, alias_to_id)
        if expanded is not None:
            out.append(expanded)
    return out


def _expand_profile(
    compact: Any, alias_to_id: dict[str, str]
) -> dict[str, Any]:
    if not isinstance(compact, dict):
        return {}
    profile: dict[str, Any] = {}
    raw_refs = compact.get("r") if isinstance(compact.get("r"), dict) else {}
    source_refs: dict[str, list[dict[str, Any]]] = {}

    for key, value in compact.items():
        if key == "r":
            continue
        field = _PROFILE_KEY_MAP.get(key)
        if field is None and key in _PROFILE_LONG_KEYS:
            field = key
        if field is None:
            continue
        if value is None:
            continue
        if field == "birth_year":
            year = _optional_birth_year(value)
            if year is not None:
                profile[field] = year
            continue
        if field in _PROFILE_TEXT_FIELDS:
            text = _optional_text(value)
            if text is not None:
                profile[field] = text
            continue
        profile[field] = value

    for ref_key, refs in raw_refs.items():
        field = _PROFILE_KEY_MAP.get(str(ref_key), str(ref_key))
        if field not in _PROFILE_LONG_KEYS:
            continue
        expanded = expand_compact_refs(refs, alias_to_id)
        if expanded:
            source_refs[field] = expanded
    if source_refs:
        profile["source_refs"] = source_refs
    return profile


def _expand_job(item: Any, alias_to_id: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    _set_optional_text(out, "raw_value", item.get("v"))
    _set_optional_text(out, "code", item.get("c"))
    _set_optional_text(out, "job_type", item.get("t"))
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _expand_skill(item: Any, alias_to_id: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    _set_optional_text(out, "raw_value", item.get("v"))
    _set_optional_text(out, "code", item.get("c"))
    # Fail-soft: omit malformed y/m (e.g. "2012.07") rather than failing validation.
    year = _optional_int(item.get("y"))
    if year is not None:
        out["last_used_year"] = year
    months = _optional_int(item.get("m"))
    if months is not None:
        out["experience_months"] = months
    # Fail-soft: omit malformed rep (e.g. free-text) rather than failing validation.
    rep = _optional_bool(item.get("rep"))
    if rep is not None:
        out["is_representative"] = rep
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _expand_expertise(
    item: Any, alias_to_id: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    _set_optional_text(out, "raw_value", item.get("v"))
    _set_optional_text(out, "code", item.get("c"))
    _set_optional_text(out, "evidence_type", item.get("e"))
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _expand_employment(
    item: Any, alias_to_id: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text_mapping = {
        "co": "company_name",
        "dp": "department",
        "ti": "title",
        "s": "start_date",
        "e": "end_date",
        "resp": "responsibilities",
    }
    out: dict[str, Any] = {}
    for short, long in text_mapping.items():
        _set_optional_text(out, long, item.get(short))
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _expand_education(
    item: Any, alias_to_id: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text_mapping = {
        "sc": "school_name",
        "mj": "major",
        "dg": "degree",
        "s": "start_date",
        "e": "end_date",
        "st": "status",
    }
    out: dict[str, Any] = {}
    for short, long in text_mapping.items():
        _set_optional_text(out, long, item.get(short))
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _expand_certification(
    item: Any, alias_to_id: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    out: dict[str, Any] = {}
    _set_optional_text(out, "certification_name", item.get("n"))
    _set_optional_text(out, "issuer", item.get("is"))
    # v10: acq/exp preferred; legacy v8/v9 ad/ex fallback.
    acquired = _optional_text(item.get("acq"))
    if acquired is None:
        acquired = _optional_text(item.get("ad"))
    if acquired is not None:
        out["acquired_date"] = acquired
    expiry = _optional_text(item.get("exp"))
    if expiry is None:
        expiry = _optional_text(item.get("ex"))
    if expiry is not None:
        out["expiry_date"] = expiry
    if item.get("f") is not None:
        out["confidence"] = item["f"]
    refs = expand_compact_refs(item.get("r"), alias_to_id)
    if refs:
        out["source_refs"] = refs
    return out if out else None


def _map_list(
    items: Any,
    expander,
    alias_to_id: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        expanded = expander(item, alias_to_id)
        if expanded is not None:
            out.append(expanded)
    return out


def expand_compact_core(
    raw: dict[str, Any],
    *,
    alias_to_id: dict[str, str],
) -> dict[str, Any]:
    """Expand compact CORE JSON into a profile-candidate-v1 dict (projects=[])."""
    summary_text = _optional_text(raw.get("sum"))
    summary: dict[str, Any] = {}
    if summary_text is not None:
        summary = {"text": summary_text}
    elif isinstance(raw.get("summary"), dict):
        summary_obj = dict(raw["summary"])
        text = _optional_text(summary_obj.get("text"))
        summary = {"text": text} if text is not None else {}

    analysis: dict[str, Any] = {}
    if raw.get("conf") is not None:
        analysis["overall_confidence"] = raw["conf"]
    elif isinstance(raw.get("analysis"), dict):
        analysis = dict(raw["analysis"])

    return {
        "schema_version": SCHEMA_VERSION,
        "profile": _expand_profile(raw.get("p"), alias_to_id),
        "jobs": _map_list(raw.get("j"), _expand_job, alias_to_id),
        "skills": _map_list(raw.get("s"), _expand_skill, alias_to_id),
        "expertise": _map_list(raw.get("x"), _expand_expertise, alias_to_id),
        "employment_history": _map_list(
            raw.get("w"), _expand_employment, alias_to_id
        ),
        "education": _map_list(raw.get("e"), _expand_education, alias_to_id),
        "certifications": _map_list(
            raw.get("c"), _expand_certification, alias_to_id
        ),
        "projects": [],
        "summary": summary,
        "analysis": analysis,
    }


def _relation_refs_for_code(
    *,
    code: str,
    project_refs: list[dict[str, Any]],
    relation_map: dict[str, Any] | None,
    relation_key: str,
    alias_to_id: dict[str, str],
    strict_relation_evidence: bool = False,
) -> list[dict[str, Any]]:
    """Prefer relation-specific compact map; else project-level refs.

    Under ``strict_relation_evidence`` (v10), j/t/x never fall back to
    project-level ``r`` when ``rm`` lacks usable refs.
    """
    if isinstance(relation_map, dict):
        bucket = relation_map.get(relation_key)
        if isinstance(bucket, dict) and code in bucket:
            refs = expand_compact_refs(bucket[code], alias_to_id)
            if refs:
                return refs
        if isinstance(bucket, list):
            refs = expand_compact_refs(bucket, alias_to_id)
            if refs:
                return refs
    if strict_relation_evidence and relation_key in _STRICT_RELATION_KEYS:
        return []
    return list(project_refs)


def _expand_code_array(
    codes: Any,
    *,
    project_refs: list[dict[str, Any]],
    relation_map: dict[str, Any] | None,
    relation_key: str,
    alias_to_id: dict[str, str],
    strict_relation_evidence: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(codes, list):
        return []
    out: list[dict[str, Any]] = []
    for item in codes:
        if isinstance(item, str):
            code = item.strip()
            if not code:
                continue
            refs = _relation_refs_for_code(
                code=code,
                project_refs=project_refs,
                relation_map=relation_map,
                relation_key=relation_key,
                alias_to_id=alias_to_id,
                strict_relation_evidence=strict_relation_evidence,
            )
            if (
                strict_relation_evidence
                and relation_key in _STRICT_RELATION_KEYS
                and not refs
            ):
                continue
            entry: dict[str, Any] = {"code": code}
            if refs:
                entry["source_refs"] = refs
            out.append(entry)
        elif isinstance(item, dict):
            # Tolerate already-expanded / hybrid shapes without inventing codes.
            code_s = _optional_text(item.get("c") or item.get("code"))
            if not code_s:
                continue
            refs = expand_compact_refs(item.get("r"), alias_to_id)
            if not refs:
                refs = _relation_refs_for_code(
                    code=code_s,
                    project_refs=project_refs,
                    relation_map=relation_map,
                    relation_key=relation_key,
                    alias_to_id=alias_to_id,
                    strict_relation_evidence=strict_relation_evidence,
                )
            if (
                strict_relation_evidence
                and relation_key in _STRICT_RELATION_KEYS
                and not refs
            ):
                continue
            entry = {"code": code_s}
            raw_value = _optional_text(item.get("v"))
            if raw_value is None:
                raw_value = _optional_text(item.get("raw_value"))
            if raw_value is not None:
                entry["raw_value"] = raw_value
            if refs:
                entry["source_refs"] = refs
            out.append(entry)
    return out


def _customer_is_catalog_code(
    customer: str,
    catalog: dict[str, tuple[str, bool]] | None,
) -> bool:
    if not catalog:
        return False
    info = catalog.get(customer)
    if info is None:
        return False
    code_type, active = info
    return bool(active) and code_type in _CUSTOMER_CODE_TYPES


def _expand_project(
    item: Any,
    alias_to_id: dict[str, str],
    *,
    strict_relation_evidence: bool = False,
    derive_duration: bool = False,
    clear_catalog_code_customer: bool = False,
    catalog: dict[str, tuple[str, bool]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    project_refs = expand_compact_refs(item.get("r"), alias_to_id)
    relation_map = item.get("rm") if isinstance(item.get("rm"), dict) else None

    out: dict[str, Any] = {}
    _set_optional_text(out, "project_name", item.get("n"))
    # v10: cust preferred; legacy cu fallback.
    customer = _optional_text(item.get("cust"))
    if customer is None:
        customer = _optional_text(item.get("cu"))
    if customer is not None:
        if clear_catalog_code_customer and _customer_is_catalog_code(
            customer, catalog
        ):
            customer = None
        if customer is not None:
            out["customer_name"] = customer
    _set_optional_text(out, "start_date", item.get("s"))
    _set_optional_text(out, "end_date", item.get("e"))
    _set_optional_text(out, "responsibilities", item.get("resp"))
    _set_optional_text(out, "project_summary", item.get("sum"))

    if derive_duration:
        # v10: ignore LLM mo/d; derive from exact YYYY.MM / YYYY-MM dates.
        duration = derive_duration_months(out.get("start_date"), out.get("end_date"))
    else:
        # v9: mo = duration_months. Legacy v8: d only when a valid integer.
        duration = _optional_int(item.get("mo"))
        if duration is None and "mo" not in item:
            duration = _optional_int(item.get("d"))
    if duration is not None:
        out["duration_months"] = duration
    if item.get("f") is not None:
        out["confidence"] = item["f"]

    for compact_key, canon_key, rel_key in (
        ("j", "jobs", "j"),
        ("t", "skills", "t"),
        ("x", "expertise", "x"),
        ("b", "business_domains", "b"),
        ("ct", "customer_types", "ct"),
    ):
        if compact_key not in item:
            continue
        codes = item.get(compact_key)
        if codes == []:
            # Explicit empty — omit (optional empty arrays not required).
            continue
        expanded = _expand_code_array(
            codes,
            project_refs=project_refs,
            relation_map=relation_map,
            relation_key=rel_key,
            alias_to_id=alias_to_id,
            strict_relation_evidence=strict_relation_evidence,
        )
        if expanded:
            out[canon_key] = expanded

    if project_refs:
        out["source_refs"] = project_refs
    return out if out else None


def is_valid_compact_projects_root(raw: dict[str, Any] | None) -> bool:
    """True when root has ``pr`` or legacy ``projects`` as a list (incl. empty).

    Bare project objects (e.g. keys n/cust/r at root) are malformed.
    Does not wrap or coerce bare roots into ``{"pr":[raw]}``.
    """
    if not isinstance(raw, dict):
        return False
    if isinstance(raw.get("pr"), list):
        return True
    if isinstance(raw.get("projects"), list):
        return True
    return False


def _count_compact_relation_codes(codes: Any) -> int:
    """Count usable compact relation codes only (no values logged)."""
    if not isinstance(codes, list):
        return 0
    count = 0
    for item in codes:
        if isinstance(item, str):
            if item.strip():
                count += 1
            continue
        if isinstance(item, dict):
            code = item.get("c") if item.get("c") is not None else item.get("code")
            if isinstance(code, str) and code.strip():
                count += 1
    return count


def _compact_projects_list(raw: dict[str, Any] | None) -> list[Any]:
    if not isinstance(raw, dict):
        return []
    projects_raw = raw.get("pr")
    if projects_raw is None:
        projects_raw = raw.get("projects")
    if not isinstance(projects_raw, list):
        return []
    return projects_raw


def count_raw_compact_project_relations(raw: dict[str, Any] | None) -> dict[str, int]:
    """Privacy-safe raw PROJECTS relation counts (keys/counts only)."""
    projects_raw = _compact_projects_list(raw)
    counts = {"projects": len(projects_raw), "j": 0, "t": 0, "x": 0, "b": 0, "ct": 0}
    for item in projects_raw:
        if not isinstance(item, dict):
            continue
        for key in ("j", "t", "x", "b", "ct"):
            counts[key] += _count_compact_relation_codes(item.get(key))
    return counts


def _count_rm_ref_entries(bucket: Any) -> int:
    """Count rm ref-shaped entries only (no values)."""
    if isinstance(bucket, list):
        count = 0
        for item in bucket:
            if isinstance(item, dict):
                count += 1
            elif isinstance(item, str) and item.strip():
                count += 1
        return count
    if isinstance(bucket, dict):
        total = 0
        for value in bucket.values():
            if isinstance(value, list):
                total += _count_rm_ref_entries(value)
            elif isinstance(value, dict):
                total += 1
        return total
    return 0


def _rm_bucket_usable(bucket: Any) -> bool:
    return _count_rm_ref_entries(bucket) > 0


def count_raw_compact_project_rm(raw: dict[str, Any] | None) -> dict[str, int]:
    """Privacy-safe raw rm presence/ref counts for j/t/x (no values)."""
    projects_raw = _compact_projects_list(raw)
    counts = {
        "rm_j_projects": 0,
        "rm_t_projects": 0,
        "rm_x_projects": 0,
        "rm_j_refs": 0,
        "rm_t_refs": 0,
        "rm_x_refs": 0,
    }
    for item in projects_raw:
        if not isinstance(item, dict):
            continue
        relation_map = item.get("rm")
        if not isinstance(relation_map, dict):
            continue
        for key, proj_key, refs_key in (
            ("j", "rm_j_projects", "rm_j_refs"),
            ("t", "rm_t_projects", "rm_t_refs"),
            ("x", "rm_x_projects", "rm_x_refs"),
        ):
            bucket = relation_map.get(key)
            refs = _count_rm_ref_entries(bucket)
            counts[refs_key] += refs
            if refs > 0 or _rm_bucket_usable(bucket):
                counts[proj_key] += 1
    return counts


def count_expanded_project_relations(
    expanded: dict[str, Any] | None,
) -> dict[str, int]:
    """Privacy-safe expanded-before-normalize project relation counts."""
    empty = {
        "projects": 0,
        "jobs": 0,
        "skills": 0,
        "expertise": 0,
        "business_domains": 0,
        "customer_types": 0,
    }
    if not isinstance(expanded, dict):
        return empty
    projects = expanded.get("projects")
    if not isinstance(projects, list):
        return empty
    counts = dict(empty)
    counts["projects"] = len(projects)
    for project in projects:
        if not isinstance(project, dict):
            continue
        counts["jobs"] += len(project["jobs"]) if isinstance(project.get("jobs"), list) else 0
        counts["skills"] += (
            len(project["skills"]) if isinstance(project.get("skills"), list) else 0
        )
        counts["expertise"] += (
            len(project["expertise"]) if isinstance(project.get("expertise"), list) else 0
        )
        counts["business_domains"] += (
            len(project["business_domains"])
            if isinstance(project.get("business_domains"), list)
            else 0
        )
        counts["customer_types"] += (
            len(project["customer_types"])
            if isinstance(project.get("customer_types"), list)
            else 0
        )
    return counts


def count_normalized_project_relations(
    candidate: ProfileCandidateDocument,
) -> dict[str, int]:
    """Privacy-safe normalized project relation counts (no values)."""
    return {
        "projects": len(candidate.projects),
        "jobs": sum(len(p.jobs) for p in candidate.projects),
        "skills": sum(len(p.skills) for p in candidate.projects),
        "expertise": sum(len(p.expertise) for p in candidate.projects),
        "business_domains": sum(
            len(p.business_domains) for p in candidate.projects
        ),
        "customer_types": sum(len(p.customer_types) for p in candidate.projects),
    }


def count_project_job_code_distribution(
    candidate: ProfileCandidateDocument | None,
) -> dict[str, int]:
    """PROJECT job code histogram (codes/counts only; no names/quotes)."""
    counts: dict[str, int] = {}
    if candidate is None:
        return counts
    for project in candidate.projects:
        for job in project.jobs:
            code = (job.code or "").strip() or "?"
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def count_project_jobs_with_quote_evidence(
    candidate: ProfileCandidateDocument | None,
) -> int:
    """Count PROJECT job relations that already have non-empty quote_text."""
    if candidate is None:
        return 0
    return sum(
        1
        for project in candidate.projects
        for job in project.jobs
        if _relation_has_quote_evidence(job)
    )


def project_relation_diagnostic_counts(
    *,
    raw: dict[str, Any] | None,
    candidate: ProfileCandidateDocument,
    expanded: dict[str, Any] | None = None,
    pre_strict: ProfileCandidateDocument | None = None,
) -> dict[str, int]:
    """Stage-aware PROJECTS relation counts (counts only; no values/quotes).

    Stages:
    - raw compact j/t/x/b/ct + rm presence/refs for j/t/x
    - expanded (after compact expand, before normalize)
    - pre_strict (after normalize/backfill, before strict quote filter)
    - candidate final (after strict quote filter when applied)
    """
    raw_counts = count_raw_compact_project_relations(raw)
    rm_counts = count_raw_compact_project_rm(raw)
    final = count_normalized_project_relations(candidate)
    if expanded is None and pre_strict is None:
        # Legacy caller without stage snapshots: do not invent stage drops.
        expanded_counts = dict(final)
        pre = dict(final)
    else:
        expanded_counts = count_expanded_project_relations(expanded)
        pre = (
            count_normalized_project_relations(pre_strict)
            if pre_strict is not None
            else dict(expanded_counts)
        )
        if expanded is None:
            expanded_counts = dict(pre)
    return {
        "projects_raw": raw_counts["projects"],
        "raw_j": raw_counts["j"],
        "raw_t": raw_counts["t"],
        "raw_x": raw_counts["x"],
        "raw_b": raw_counts["b"],
        "raw_ct": raw_counts["ct"],
        "raw_rm_j_projects": rm_counts["rm_j_projects"],
        "raw_rm_t_projects": rm_counts["rm_t_projects"],
        "raw_rm_x_projects": rm_counts["rm_x_projects"],
        "raw_rm_j_refs": rm_counts["rm_j_refs"],
        "raw_rm_t_refs": rm_counts["rm_t_refs"],
        "raw_rm_x_refs": rm_counts["rm_x_refs"],
        "expanded_projects": expanded_counts["projects"],
        "expanded_jobs": expanded_counts["jobs"],
        "expanded_skills": expanded_counts["skills"],
        "expanded_expertise": expanded_counts["expertise"],
        "expanded_business_domains": expanded_counts["business_domains"],
        "expanded_customer_types": expanded_counts["customer_types"],
        "pre_strict_projects": pre["projects"],
        "pre_strict_jobs": pre["jobs"],
        "pre_strict_skills": pre["skills"],
        "pre_strict_expertise": pre["expertise"],
        "pre_strict_business_domains": pre["business_domains"],
        "pre_strict_customer_types": pre["customer_types"],
        "norm_projects": final["projects"],
        "norm_jobs": final["jobs"],
        "norm_skills": final["skills"],
        "norm_expertise": final["expertise"],
        "norm_business_domains": final["business_domains"],
        "norm_customer_types": final["customer_types"],
        "dropped_j": max(0, raw_counts["j"] - final["jobs"]),
        "dropped_t": max(0, raw_counts["t"] - final["skills"]),
        "dropped_x": max(0, raw_counts["x"] - final["expertise"]),
        "dropped_b": max(0, raw_counts["b"] - final["business_domains"]),
        "dropped_ct": max(0, raw_counts["ct"] - final["customer_types"]),
        "dropped_during_expand_j": max(0, raw_counts["j"] - expanded_counts["jobs"]),
        "dropped_during_expand_t": max(0, raw_counts["t"] - expanded_counts["skills"]),
        "dropped_during_expand_x": max(0, raw_counts["x"] - expanded_counts["expertise"]),
        "dropped_during_normalize_j": max(
            0, expanded_counts["jobs"] - pre["jobs"]
        ),
        "dropped_during_normalize_t": max(
            0, expanded_counts["skills"] - pre["skills"]
        ),
        "dropped_during_normalize_x": max(
            0, expanded_counts["expertise"] - pre["expertise"]
        ),
        "dropped_during_strict_filter_j": max(0, pre["jobs"] - final["jobs"]),
        "dropped_during_strict_filter_t": max(0, pre["skills"] - final["skills"]),
        "dropped_during_strict_filter_x": max(
            0, pre["expertise"] - final["expertise"]
        ),
    }


def expand_compact_projects(
    raw: dict[str, Any],
    *,
    alias_to_id: dict[str, str],
    strict_relation_evidence: bool = False,
    derive_duration: bool = False,
    clear_catalog_code_customer: bool = False,
    catalog: dict[str, tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """Expand compact PROJECTS JSON into a projects-only candidate-v1 dict."""
    projects_raw = raw.get("pr")
    if projects_raw is None:
        projects_raw = raw.get("projects")
    if not isinstance(projects_raw, list):
        return {"schema_version": SCHEMA_VERSION, "projects": []}
    projects: list[dict[str, Any]] = []
    for item in projects_raw:
        expanded = _expand_project(
            item,
            alias_to_id,
            strict_relation_evidence=strict_relation_evidence,
            derive_duration=derive_duration,
            clear_catalog_code_customer=clear_catalog_code_customer,
            catalog=catalog,
        )
        if expanded is not None:
            projects.append(expanded)
    return {
        "schema_version": SCHEMA_VERSION,
        "projects": projects,
    }


def _source_ref_has_quote(ref: SourceRef) -> bool:
    return bool((ref.quote_text or "").strip())


def _relation_has_quote_evidence(rel: CodeRefCandidate) -> bool:
    return any(_source_ref_has_quote(ref) for ref in (rel.source_refs or []))


_CORE_PROFILE_BACKFILL_FIELDS = (
    "name",
    "phone",
    "email",
    "address_region",
    "current_title",
    "career_document_value",
)


def _exact_page_match(
    value: str | None,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
) -> SourceRef | None:
    """First deterministic exact substring match in stable (doc, page) order."""
    text = (value or "").strip()
    if not text:
        return None
    for doc_id, page_no in sorted(page_texts.keys()):
        if doc_id not in allowed_documents:
            continue
        pages = allowed_documents[doc_id]
        if pages and page_no not in pages:
            continue
        page_text = page_texts.get((doc_id, page_no)) or ""
        if text in page_text:
            return SourceRef(document_id=doc_id, page_no=page_no, quote_text=text)
    return None


def _refs_nonempty(refs: list[SourceRef] | None) -> bool:
    return any((ref.quote_text or "").strip() for ref in (refs or []))


# v20 semantic EXP phrases (CORE backfill + PROJECT relation guard).
# Higher strength wins for backfill; ties break by sorted (document_id, page_no).
_EXP_SEMANTIC_PATTERNS: dict[str, tuple[tuple[int, str, tuple[str, ...]], ...]] = {
    "EXP-INFRA": (
        # quote phrase, optional same-text companions
        (3, "시스템구축 및 운영", ("IT시스템기술지원", "IT시스템관리")),
        (2, "정보시스템 운영", ()),
        (1, "시스템 운영", ()),
    ),
    "EXP-MGT": ((1, "사업관리", ()),),
    "EXP-SEC-OPS": ((1, "보안 운영", ()),),
    "EXP-SEC-BUILD": ((1, "보안 구축", ()),),
}


def _quote_supports_semantic_exp(code: str, quote: str) -> bool:
    """True when quote text itself supports a known EXP prompt-contract phrase."""
    patterns = _EXP_SEMANTIC_PATTERNS.get(code)
    if not patterns:
        return False
    text = (quote or "").strip()
    if not text:
        return False
    for _strength, phrase, companions in patterns:
        if phrase not in text:
            continue
        if companions and not any(req in text for req in companions):
            continue
        return True
    return False


def _semantic_exp_page_match(
    code: str,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
) -> SourceRef | None:
    """Strongest prompt-contract phrase match for a known EXP code."""
    patterns = _EXP_SEMANTIC_PATTERNS.get(code)
    if not patterns:
        return None
    best: tuple[int, str, int, str] | None = None
    for doc_id, page_no in sorted(page_texts.keys()):
        if doc_id not in allowed_documents:
            continue
        pages = allowed_documents[doc_id]
        if pages and page_no not in pages:
            continue
        page_text = page_texts.get((doc_id, page_no)) or ""
        if not page_text:
            continue
        for strength, quote, companions in patterns:
            if quote not in page_text:
                continue
            if companions and not any(req in page_text for req in companions):
                continue
            # Sort key: higher strength first, then earlier doc/page.
            key = (-strength, doc_id, page_no, quote)
            if best is None or key < best:
                best = key
    if best is None:
        return None
    _, doc_id, page_no, quote = best
    return SourceRef(document_id=doc_id, page_no=page_no, quote_text=quote)


def backfill_semantic_core_expertise_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v20: fill empty root expertise source_refs via prompt-contract phrases.

    Only known EXP codes (INFRA/MGT/SEC-OPS/SEC-BUILD). Never overwrites
    existing refs. Does not change raw_value/code/evidence_type/confidence.
    """
    if not page_texts:
        return candidate
    changed = False
    new_expertise: list[ExpertiseCandidate] = []
    for exp in candidate.expertise:
        if _refs_nonempty(exp.source_refs):
            new_expertise.append(exp)
            continue
        code = (exp.code or "").strip()
        match = _semantic_exp_page_match(
            code,
            page_texts=page_texts,
            allowed_documents=allowed_documents,
        )
        if match is None:
            new_expertise.append(exp)
            continue
        changed = True
        new_expertise.append(exp.model_copy(update={"source_refs": [match]}))
    if not changed:
        return candidate
    return candidate.model_copy(update={"expertise": new_expertise})


def backfill_exact_core_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v13: backfill empty CORE source_refs via exact page-text substring match.

    - CORE entities only; projects untouched.
    - Existing non-empty refs always win.
    - Exact literal match only; no fuzzy/alias/inference.
    - affiliation_company is never backfilled.
    """
    if not page_texts:
        return candidate

    def _one(value: str | None, refs: list[SourceRef]) -> list[SourceRef]:
        if _refs_nonempty(refs):
            return list(refs)
        match = _exact_page_match(
            value,
            page_texts=page_texts,
            allowed_documents=allowed_documents,
        )
        return [match] if match is not None else list(refs)

    profile_refs = dict(candidate.profile.source_refs or {})
    profile_changed = False
    for field in _CORE_PROFILE_BACKFILL_FIELDS:
        existing = profile_refs.get(field) or []
        if _refs_nonempty(existing):
            continue
        scalar = getattr(candidate.profile, field, None)
        if not isinstance(scalar, str):
            continue
        match = _exact_page_match(
            scalar,
            page_texts=page_texts,
            allowed_documents=allowed_documents,
        )
        if match is None:
            continue
        profile_refs[field] = [match]
        profile_changed = True

    profile = (
        candidate.profile.model_copy(update={"source_refs": profile_refs})
        if profile_changed
        else candidate.profile
    )

    jobs = [
        job.model_copy(update={"source_refs": _one(job.raw_value, job.source_refs)})
        for job in candidate.jobs
    ]
    skills = [
        skill.model_copy(
            update={"source_refs": _one(skill.raw_value, skill.source_refs)}
        )
        for skill in candidate.skills
    ]
    expertise = [
        exp.model_copy(update={"source_refs": _one(exp.raw_value, exp.source_refs)})
        for exp in candidate.expertise
    ]
    employment = [
        row.model_copy(
            update={"source_refs": _one(row.company_name, row.source_refs)}
        )
        for row in candidate.employment_history
    ]
    education = [
        row.model_copy(
            update={"source_refs": _one(row.school_name, row.source_refs)}
        )
        for row in candidate.education
    ]
    certifications = [
        row.model_copy(
            update={
                "source_refs": _one(row.certification_name, row.source_refs)
            }
        )
        for row in candidate.certifications
    ]

    if (
        profile is candidate.profile
        and jobs == list(candidate.jobs)
        and skills == list(candidate.skills)
        and expertise == list(candidate.expertise)
        and employment == list(candidate.employment_history)
        and education == list(candidate.education)
        and certifications == list(candidate.certifications)
    ):
        return candidate
    return candidate.model_copy(
        update={
            "profile": profile,
            "jobs": jobs,
            "skills": skills,
            "expertise": expertise,
            "employment_history": employment,
            "education": education,
            "certifications": certifications,
        }
    )


def promote_exact_root_catalog_codes(
    candidate: ProfileCandidateDocument,
    *,
    catalog: dict[str, tuple[str, bool]],
) -> ProfileCandidateDocument:
    """v11-only: if root code is null and raw_value exactly matches an active
    catalog code of the expected type, set code to that exact value.

    No alias/fuzzy/name inference. Wrong type / inactive / unknown stay null.
    Applies only to root jobs / skills / expertise (not project relations).
    """

    def _promote_one(
        code: str | None,
        raw_value: str | None,
        *,
        expected_type: str,
    ) -> str | None:
        if code is not None and str(code).strip():
            return code
        raw = (raw_value or "").strip()
        if not raw:
            return None
        entry = catalog.get(raw)
        if entry is None:
            return None
        code_type, is_active = entry
        if not is_active or code_type != expected_type:
            return None
        return raw

    jobs: list[JobCandidate] = []
    for job in candidate.jobs:
        promoted = _promote_one(job.code, job.raw_value, expected_type="JOB")
        jobs.append(job if promoted == job.code else job.model_copy(update={"code": promoted}))

    skills: list[SkillCandidate] = []
    for skill in candidate.skills:
        promoted = _promote_one(skill.code, skill.raw_value, expected_type="TECH")
        skills.append(
            skill if promoted == skill.code else skill.model_copy(update={"code": promoted})
        )

    expertise: list[ExpertiseCandidate] = []
    for exp in candidate.expertise:
        promoted = _promote_one(exp.code, exp.raw_value, expected_type="EXP")
        expertise.append(
            exp if promoted == exp.code else exp.model_copy(update={"code": promoted})
        )

    if (
        jobs == list(candidate.jobs)
        and skills == list(candidate.skills)
        and expertise == list(candidate.expertise)
    ):
        return candidate
    return candidate.model_copy(
        update={"jobs": jobs, "skills": skills, "expertise": expertise}
    )


# Folded exact phrases that are work/activity-only (never TECH by themselves).
_GENERIC_ACTIVITY_CORE_TECH_FOLDED: frozenset[str] = frozenset(
    {
        "운영",
        "구축",
        "유지보수",
        "기술지원",
        "백업",
        "이관",
        "사업관리",
        "정보자원통합구축",
        "정보시스템운영",
        "시스템운영",
        "시스템구축",
        "시스템구축및운영",
    }
)


def fold_generic_activity_tech_text(value: str) -> str:
    """Normalize for generic-activity TECH matching (whitespace/punct only)."""
    text = unicodedata.normalize("NFKC", value).casefold()
    out: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        if unicodedata.category(ch).startswith("P"):
            continue
        out.append(ch)
    return "".join(out)


def is_generic_activity_core_tech_raw(raw_value: str | None) -> bool:
    """True when raw_value is only a known work/activity phrase (fail-closed)."""
    if not isinstance(raw_value, str):
        return False
    folded = fold_generic_activity_tech_text(raw_value)
    if not folded:
        return False
    return folded in _GENERIC_ACTIVITY_CORE_TECH_FOLDED


def drop_generic_activity_core_skills(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: remove root skills whose raw_value is an activity-only phrase.

    Project relation skills are untouched. JOB/EXP and non-matching TECH stay.
    Drops even when ``code`` is set if raw_value itself is generic activity.
    """
    kept = [
        skill
        for skill in candidate.skills
        if not is_generic_activity_core_tech_raw(skill.raw_value)
    ]
    if len(kept) == len(candidate.skills):
        return candidate
    return candidate.model_copy(update={"skills": kept})


def drop_core_skills_without_quote_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: drop root skills that still lack non-empty quote evidence.

    Intended after ``backfill_exact_core_evidence``. Valid TECH codes without
    any quoted source_ref are removed. Project relation t[] / JOB / EXP stay.
    """
    kept = [
        skill
        for skill in candidate.skills
        if _refs_nonempty(skill.source_refs)
    ]
    if len(kept) == len(candidate.skills):
        return candidate
    return candidate.model_copy(update={"skills": kept})


def _norm_tech_literal_key(value: str | None) -> str | None:
    text = unicodedata.normalize("NFKC", (value or "")).strip()
    if not text:
        return None
    return text.casefold()


def _is_ascii_alnum_tech_token(literal: str) -> bool:
    """True for short ASCII tokens like AD/NAC/SEP that need alnum boundaries."""
    return bool(literal) and literal.isascii() and literal.isalnum()


def _find_tech_literal_in_page(page_text: str, literal: str) -> str | None:
    """Return matched quote from page (original NFKC casing) or None.

    ASCII alnum tokens use alnum boundaries (ROAD must not match AD).
    Other literals use case-insensitive NFKC substring match. No fuzzy.
    """
    lit = unicodedata.normalize("NFKC", literal).strip()
    if not lit:
        return None
    page = unicodedata.normalize("NFKC", page_text or "")
    if not page:
        return None
    if _is_ascii_alnum_tech_token(lit):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9])({re.escape(lit)})(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
    else:
        pattern = re.compile(f"({re.escape(lit)})", re.IGNORECASE)
    match = pattern.search(page)
    if match is None:
        return None
    return match.group(1)


def _find_tech_literal_page_ref(
    literal: str,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
) -> SourceRef | None:
    """First deterministic page hit for ``literal`` in stable (doc, page) order."""
    for doc_id, page_no in sorted(page_texts.keys()):
        if doc_id not in allowed_documents:
            continue
        pages = allowed_documents[doc_id]
        if pages and page_no not in pages:
            continue
        quote = _find_tech_literal_in_page(
            page_texts.get((doc_id, page_no)) or "", literal
        )
        if quote is None:
            continue
        return SourceRef(document_id=doc_id, page_no=page_no, quote_text=quote)
    return None


def backfill_literal_core_tech_from_source(
    candidate: ProfileCandidateDocument,
    *,
    codes: list[Any],
    aliases: dict[str, list[str]],
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v20: repair/supplement root TECH skills from explicit source literals.

    Only active TECH leaf codes (``parent_code`` set) are candidates. Literals
    are CodeMaster.name and code_alias.alias. Ambiguous literals (same text →
    multiple TECH codes) are skipped.

    - Existing skill with valid quote evidence → unchanged.
    - Existing skill with empty quote evidence → ``source_refs`` only repaired
      when a unique literal matches (raw_value/code/periods untouched).
    - Missing skill code → append a new SkillCandidate with quote evidence.
    """
    if not page_texts or not codes:
        return candidate

    leaf_techs = [
        row
        for row in codes
        if getattr(row, "code_type", None) == "TECH"
        and getattr(row, "parent_code", None)
        and (getattr(row, "code", None) or "").strip()
        and (getattr(row, "name", None) or "").strip()
    ]
    if not leaf_techs:
        return candidate

    # literal_key → codes that claim it (for fail-closed ambiguity).
    literal_to_codes: dict[str, set[str]] = {}
    code_literals: dict[str, list[str]] = {}
    for row in leaf_techs:
        code = str(row.code).strip()
        lits: list[str] = []
        seen_keys: set[str] = set()
        for raw in (row.name, *(aliases.get(code) or ())):
            text = unicodedata.normalize("NFKC", str(raw or "")).strip()
            key = _norm_tech_literal_key(text)
            if key is None or key in seen_keys:
                continue
            seen_keys.add(key)
            lits.append(text)
            literal_to_codes.setdefault(key, set()).add(code)
        code_literals[code] = lits

    def _unique_literals_for_code(code: str) -> list[str]:
        return sorted(
            (
                lit
                for lit in code_literals.get(code, [])
                if literal_to_codes.get(_norm_tech_literal_key(lit) or "")
                == {code}
            ),
            key=lambda s: s.casefold(),
        )

    def _first_literal_ref(code: str) -> SourceRef | None:
        for lit in _unique_literals_for_code(code):
            ref = _find_tech_literal_page_ref(
                lit,
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if ref is not None:
                return ref
        return None

    # Repair empty evidence on existing leaf TECH skills (no field overwrite).
    skills_out: list[SkillCandidate] = []
    repaired = False
    present_codes: set[str] = set()
    for skill in candidate.skills:
        code = (skill.code or "").strip()
        if code:
            present_codes.add(code)
        if (
            code
            and code in code_literals
            and not _refs_nonempty(skill.source_refs)
        ):
            ref = _first_literal_ref(code)
            if ref is not None:
                skills_out.append(
                    skill.model_copy(update={"source_refs": [ref]})
                )
                repaired = True
                continue
        skills_out.append(skill)

    # Supplement missing leaf TECH codes (unchanged create path).
    extras: list[SkillCandidate] = []
    for row in sorted(leaf_techs, key=lambda r: str(r.code)):
        code = str(row.code).strip()
        if code in present_codes:
            continue
        ref = _first_literal_ref(code)
        if ref is None:
            continue
        extras.append(
            SkillCandidate(
                code=code,
                raw_value=ref.quote_text,
                source_refs=[ref],
            )
        )
        present_codes.add(code)

    if not repaired and not extras:
        return candidate
    return candidate.model_copy(update={"skills": skills_out + extras})


def _project_evidence_anchors(project: ProjectCandidate) -> tuple[str, ...]:
    """Scalar anchors for project root evidence, in priority order."""
    out: list[str] = []
    for scalar in (
        project.project_name,
        project.customer_name,
        project.responsibilities,
        project.project_summary,
    ):
        if isinstance(scalar, str):
            text = scalar.strip()
            if text:
                out.append(text)
    return tuple(out)


def _unique_exact_page_ref(
    quote: str,
    *,
    page_texts: dict[tuple[str, int], str],
) -> SourceRef | None:
    """Return a root ref only when ``quote`` matches exactly one page.

    Ambiguous multi-page hits are rejected (no arbitrary first-page pick).
    """
    text = (quote or "").strip()
    if not text:
        return None
    hits: list[tuple[str, int]] = []
    for doc_id, page_no in sorted(page_texts.keys()):
        page_text = page_texts.get((doc_id, page_no)) or ""
        if text in page_text:
            hits.append((doc_id, page_no))
    if len(hits) != 1:
        return None
    doc_id, page_no = hits[0]
    return SourceRef(document_id=doc_id, page_no=page_no, quote_text=text)


def backfill_exact_project_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
) -> tuple[ProfileCandidateDocument, int]:
    """v13: repair quote-less / empty project ROOT refs via exact page scalars.

    1. Existing non-empty quotes always win.
    2. Quote-less refs with valid document_id/page_no: try exact anchors on
       THAT page only (project_name → customer_name → responsibilities →
       project_summary).
    3. Empty ``source_refs``: search allowed ``page_texts`` for an exact
       unique page match of those anchors; create one root ref from the
       matched page. Ambiguous multi-page hits are not repaired.

    - Project ``source_refs`` only; j/t/x relation refs are never modified.
    - No fuzzy match or invented text. Dates alone are never anchors.

    Returns ``(candidate, root_refs_repaired)``.
    """
    if not page_texts or not candidate.projects:
        return candidate, 0

    repaired = 0
    projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        refs = list(project.source_refs or [])
        anchors = _project_evidence_anchors(project)

        # Empty refs (discarded/missing r): unique exact page search.
        if not refs:
            created: SourceRef | None = None
            for text in anchors:
                created = _unique_exact_page_ref(text, page_texts=page_texts)
                if created is not None:
                    break
            if created is not None:
                projects.append(
                    project.model_copy(update={"source_refs": [created]})
                )
                repaired += 1
            else:
                projects.append(project)
            continue

        new_refs: list[SourceRef] = []
        changed = False
        for ref in refs:
            if _source_ref_has_quote(ref):
                new_refs.append(ref)
                continue
            doc_id = ref.document_id
            page_no = ref.page_no
            if not doc_id or page_no is None:
                new_refs.append(ref)
                continue
            page_text = page_texts.get((doc_id, page_no)) or ""
            if not page_text:
                new_refs.append(ref)
                continue
            quote: str | None = None
            for text in anchors:
                if text in page_text:
                    quote = text
                    break
            if quote is None:
                new_refs.append(ref)
                continue
            new_refs.append(ref.model_copy(update={"quote_text": quote}))
            repaired += 1
            changed = True
        if changed:
            projects.append(project.model_copy(update={"source_refs": new_refs}))
        else:
            projects.append(project)

    if repaired == 0:
        return candidate, 0
    return candidate.model_copy(update={"projects": projects}), repaired


def _whitespace_folded(text: str) -> str:
    """Remove Unicode whitespace only (``str.isspace()``); no case/char folding."""
    return "".join(ch for ch in text if not ch.isspace())


def find_unique_whitespace_insensitive_span(
    page_text: str, quote: str
) -> str | None:
    """Return the unique original page span matching quote ignoring whitespace.

    Fail-closed: zero or multiple matches → ``None``. Exact substring matches
    are handled by the caller (left unchanged).
    """
    needle = _whitespace_folded(quote)
    if not needle:
        return None
    matches: list[str] = []
    n = len(page_text)
    needle_len = len(needle)
    i = 0
    while i < n:
        if page_text[i].isspace():
            i += 1
            continue
        folded: list[str] = []
        j = i
        while j < n and len(folded) < needle_len:
            ch = page_text[j]
            if not ch.isspace():
                folded.append(ch)
            j += 1
        if len(folded) == needle_len and "".join(folded) == needle:
            matches.append(page_text[i:j])
            if len(matches) > 1:
                return None
        i += 1
    if len(matches) == 1:
        return matches[0]
    return None


def _repair_ref_quote_whitespace(
    ref: dict[str, Any],
    *,
    page_texts: dict[tuple[str, int], str],
) -> tuple[dict[str, Any], bool]:
    """Repair one expanded source_ref quote in place semantics; return (ref, changed)."""
    quote = ref.get("quote_text")
    if not isinstance(quote, str):
        return ref, False
    quote = quote.strip()
    if not quote:
        return ref, False
    doc_id = ref.get("document_id")
    page_no = ref.get("page_no")
    if not isinstance(doc_id, str) or not doc_id.strip():
        return ref, False
    if not isinstance(page_no, int):
        return ref, False
    page_text = page_texts.get((doc_id, page_no))
    if not isinstance(page_text, str) or not page_text:
        return ref, False
    if quote in page_text:
        return ref, False
    span = find_unique_whitespace_insensitive_span(page_text, quote)
    if span is None:
        return ref, False
    if span == quote:
        return ref, False
    repaired = dict(ref)
    repaired["quote_text"] = span
    return repaired, True


def repair_whitespace_project_evidence(
    expanded: dict[str, Any],
    *,
    page_texts: dict[tuple[str, int], str] | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """v19: repair PROJECTS quotes that differ only by Unicode whitespace.

    Runs on expanded compact PROJECTS (before ``normalize_candidate``).
    Only existing non-empty quotes on project root / jobs / skills / expertise
    refs are considered. Fail-closed unique match on the referenced page only.
    Never copies root evidence into j/t/x or invents text.

    Returns ``(expanded, counts)`` with counts-only diagnostics.
    """
    counts = {
        "whitespace_repaired_root_refs": 0,
        "whitespace_repaired_j_refs": 0,
        "whitespace_repaired_t_refs": 0,
        "whitespace_repaired_x_refs": 0,
    }
    if not page_texts or not isinstance(expanded, dict):
        return expanded, counts
    projects = expanded.get("projects")
    if not isinstance(projects, list) or not projects:
        return expanded, counts

    new_projects: list[Any] = []
    changed_any = False
    for project in projects:
        if not isinstance(project, dict):
            new_projects.append(project)
            continue
        proj = dict(project)
        proj_changed = False

        root_refs = proj.get("source_refs")
        if isinstance(root_refs, list):
            repaired_refs: list[Any] = []
            root_changed = False
            for ref in root_refs:
                if not isinstance(ref, dict):
                    repaired_refs.append(ref)
                    continue
                new_ref, changed = _repair_ref_quote_whitespace(
                    ref, page_texts=page_texts
                )
                repaired_refs.append(new_ref)
                if changed:
                    counts["whitespace_repaired_root_refs"] += 1
                    root_changed = True
            if root_changed:
                proj["source_refs"] = repaired_refs
                proj_changed = True

        for field, count_key in (
            ("jobs", "whitespace_repaired_j_refs"),
            ("skills", "whitespace_repaired_t_refs"),
            ("expertise", "whitespace_repaired_x_refs"),
        ):
            relations = proj.get(field)
            if not isinstance(relations, list):
                continue
            new_relations: list[Any] = []
            field_changed = False
            for rel in relations:
                if not isinstance(rel, dict):
                    new_relations.append(rel)
                    continue
                rel_refs = rel.get("source_refs")
                if not isinstance(rel_refs, list):
                    new_relations.append(rel)
                    continue
                new_rel_refs: list[Any] = []
                rel_changed = False
                for ref in rel_refs:
                    if not isinstance(ref, dict):
                        new_rel_refs.append(ref)
                        continue
                    new_ref, changed = _repair_ref_quote_whitespace(
                        ref, page_texts=page_texts
                    )
                    new_rel_refs.append(new_ref)
                    if changed:
                        counts[count_key] += 1
                        rel_changed = True
                if rel_changed:
                    new_rel = dict(rel)
                    new_rel["source_refs"] = new_rel_refs
                    new_relations.append(new_rel)
                    field_changed = True
                else:
                    new_relations.append(rel)
            if field_changed:
                proj[field] = new_relations
                proj_changed = True

        if proj_changed:
            changed_any = True
            new_projects.append(proj)
        else:
            new_projects.append(project)

    if not changed_any:
        return expanded, counts
    out = dict(expanded)
    out["projects"] = new_projects
    return out, counts


# PROJECT JOB-MGT-PM / JOB-MGT-PL require a literal PM/PL token in some quote.
# ASCII alnum boundaries: allow "PM", "[PM]", "PM 사업관리", "PM/PL"; reject "PMO".
_PM_LITERAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])PM(?![A-Za-z0-9])", re.IGNORECASE)
_PL_LITERAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])PL(?![A-Za-z0-9])", re.IGNORECASE)
_TA_LITERAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])TA(?![A-Za-z0-9])", re.IGNORECASE)
_PM_PL_LITERAL_JOB_PATTERNS: dict[str, re.Pattern[str]] = {
    "JOB-MGT-PM": _PM_LITERAL_TOKEN_RE,
    "JOB-MGT-PL": _PL_LITERAL_TOKEN_RE,
}
# JOB codes eligible for v20 semantic project-row evidence backfill.
# OPS-SYS/MGT reuse EXP-INFRA/EXP-MGT phrase rules; TA/PM/PL use literals.
_JOB_SEMANTIC_EXP_ALIAS: dict[str, str] = {
    "JOB-OPS-SYS": "EXP-INFRA",
    "JOB-MGT": "EXP-MGT",
}
_JOB_LITERAL_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "JOB-ARC-TA": _TA_LITERAL_TOKEN_RE,
    "JOB-MGT-PM": _PM_LITERAL_TOKEN_RE,
    "JOB-MGT-PL": _PL_LITERAL_TOKEN_RE,
}
_JOB_SEMANTIC_BACKFILL_CODES = frozenset(_JOB_SEMANTIC_EXP_ALIAS) | frozenset(
    _JOB_LITERAL_TOKEN_PATTERNS
)


def _project_job_has_literal_pm_pl_evidence(rel: CodeRefCandidate) -> bool:
    """True when job is not PM/PL, or at least one quote has the required token."""
    pattern = _PM_PL_LITERAL_JOB_PATTERNS.get((rel.code or "").strip())
    if pattern is None:
        return True
    for ref in rel.source_refs or []:
        quote = (ref.quote_text or "").strip()
        if quote and pattern.search(quote):
            return True
    return False


# Education status is free-form in schema; repo prompts/tests use Korean
# 졸업/재학중/졸예. LLM may also emit GRADUATED/ENROLLED. Unknown → drop.
_EDUCATION_STATUS_LITERALS: dict[str, tuple[str, ...]] = {
    "GRADUATED": ("졸업",),
    "졸업": ("졸업",),
    "ENROLLED": ("재학",),
    "재학": ("재학",),
    "재학중": ("재학",),
    "졸예": ("졸업예정", "졸예"),
    "졸업예정": ("졸업예정", "졸예"),
}


def _education_status_literals(status: str) -> tuple[str, ...] | None:
    key = status.strip()
    if not key:
        return None
    if key in _EDUCATION_STATUS_LITERALS:
        return _EDUCATION_STATUS_LITERALS[key]
    return _EDUCATION_STATUS_LITERALS.get(key.upper())


def _education_status_has_literal_evidence(row: EducationCandidate) -> bool:
    status = (row.status or "").strip()
    if not status:
        return True
    literals = _education_status_literals(status)
    if literals is None:
        return False
    for ref in row.source_refs or []:
        quote = ref.quote_text or ""
        if any(lit in quote for lit in literals):
            return True
    return False


def clear_education_status_without_literal_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: clear EducationCandidate.status lacking literal status-word quotes.

    Keeps the education row and school/major/degree/dates/source_refs.
    Degree words alone (학사/석사/…) are not status evidence. Unknown status
    values are fail-closed cleared.
    """
    changed = False
    new_rows: list[EducationCandidate] = []
    for row in candidate.education:
        if _education_status_has_literal_evidence(row):
            new_rows.append(row)
            continue
        changed = True
        new_rows.append(row.model_copy(update={"status": None}))
    if not changed:
        return candidate
    return candidate.model_copy(update={"education": new_rows})


def normalize_career_document_value(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: rewrite parseable profile.career_document_value to duration-only text.

    Leaves technical_grade, source_refs, and all other fields unchanged.
    """
    from app.modules.analysis.career_document_months import (
        normalize_career_document_value_text,
    )

    raw = candidate.profile.career_document_value
    normalized = normalize_career_document_value_text(raw)
    if normalized == raw:
        return candidate
    return candidate.model_copy(
        update={
            "profile": candidate.profile.model_copy(
                update={"career_document_value": normalized}
            )
        }
    )


_LINE_DATE_YM_RE = re.compile(
    r"(?<!\d)(\d{4})[./\-](\d{1,2})(?:[./\-]\d{1,2})?(?!\d)"
)
_PROJECT_ROW_RANGE_MARK_RE = re.compile(r"[~～]")
# Project-row shape markers (name/customer/period table rows). Absence plus
# employment section/title cues means the line is not usable as project evidence.
_PROJECT_ROW_STRUCTURE_RE = re.compile(
    r"사업|프로젝트|구축|정보화|BIA|BCP|이전통합|업무분석|사업관리|"
    r"시스템\s*구축|IT시스템"
)
_EMPLOYMENT_SECTION_RE = re.compile(r"근무경력|재직기간")


def _fold_ws_text(value: str) -> str:
    return "".join(ch for ch in value if not ch.isspace())


def _line_is_employment_history_row(
    line: str, *, name_matched: bool = False
) -> bool:
    """True when a dated line is employment history, not a project row.

    Project-name association or explicit project-structure markers keep the
    line eligible. Employment section headers / title-only company rows are
    rejected so they cannot backfill project BIZ/EXP evidence.
    """
    if name_matched:
        return False
    if _EMPLOYMENT_SECTION_RE.search(line):
        return True
    if _PROJECT_ROW_STRUCTURE_RE.search(line):
        return False
    # Customer/company + period + job title (or no project markers at all).
    return True


def _project_start_year_month(project: ProjectCandidate) -> tuple[int, int] | None:
    """Candidate start as (year, month); accepts YYYY.MM or YYYY.MM.DD forms."""
    ym = parse_year_month(project.start_date)
    if ym is not None:
        return ym
    text = _optional_text(project.start_date)
    if text is None:
        return None
    match = _LINE_DATE_YM_RE.search(text)
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    if month < 1 or month > 12:
        return None
    return year, month


def _line_has_year_month(line: str, year: int, month: int) -> bool:
    for match in _LINE_DATE_YM_RE.finditer(line):
        if int(match.group(1)) == year and int(match.group(2)) == month:
            return True
    return False


def _quote_matches_project_start_month(
    project: ProjectCandidate, quote: str
) -> bool:
    """True when quote start month equals candidate start (YYYY.MM or YY.MM)."""
    start_ym = _project_start_year_month(project)
    if start_ym is None:
        return False
    year, month = start_ym
    if _line_has_year_month(quote, year, month):
        return True
    # PPTX root evidence often uses multi-line YY.MM ~ YY.MM spans.
    parsed = parse_project_date_range_from_texts(
        [quote], project_name=project.project_name
    )
    if parsed is None:
        return False
    return parse_year_month(parsed[0]) == start_ym


def _line_has_ws_folded_phrase(line: str, phrase: str) -> bool:
    needle = _fold_ws_text(phrase.strip())
    if not needle:
        return False
    return needle in _fold_ws_text(line)


def _extract_semantic_exp_quote(code: str, text: str) -> str | None:
    """Shortest actual substring in ``text`` that supports the EXP code."""
    patterns = _EXP_SEMANTIC_PATTERNS.get(code)
    if not patterns:
        return None
    for _strength, phrase, companions in patterns:
        if phrase not in text:
            continue
        if not companions:
            return phrase
        for req in companions:
            if req not in text:
                continue
            i_phrase = text.find(phrase)
            i_req = text.find(req)
            start = min(i_phrase, i_req)
            end = max(i_phrase + len(phrase), i_req + len(req))
            span = text[start:end].strip()
            if span and _quote_supports_semantic_exp(code, span):
                return span
    return None


def _iter_allowed_page_lines(
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
):
    for doc_id, page_no in sorted(page_texts.keys()):
        if doc_id not in allowed_documents:
            continue
        pages = allowed_documents[doc_id]
        if pages and page_no not in pages:
            continue
        page_text = page_texts.get((doc_id, page_no)) or ""
        for raw_line in page_text.splitlines():
            line = raw_line.strip()
            if line:
                yield doc_id, page_no, line


def _find_unique_project_source_row(
    project: ProjectCandidate,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
    line_predicate: Any | None = None,
) -> tuple[str, int, str] | None:
    """Fail-closed unique project source line for evidence backfill.

    Requires start month precision, a ``~`` date-range marker, and either a
    unique project_name (exact/ws-fold) hit or a unique customer+start hit.
    ``line_predicate`` may further restrict candidate lines (e.g. EXP phrases).
    """
    start_ym = _project_start_year_month(project)
    if start_ym is None:
        return None
    year, month = start_ym
    name = (project.project_name or "").strip() or None
    customer = (project.customer_name or "").strip() or None
    if name is None and customer is None:
        return None

    named_hits: list[tuple[str, int, str]] = []
    customer_hits: list[tuple[str, int, str]] = []
    for doc_id, page_no, line in _iter_allowed_page_lines(
        page_texts, allowed_documents
    ):
        if not _PROJECT_ROW_RANGE_MARK_RE.search(line):
            continue
        if not _line_has_year_month(line, year, month):
            continue
        if line_predicate is not None and not line_predicate(line):
            continue
        name_matched = bool(
            name is not None
            and (name in line or _line_has_ws_folded_phrase(line, name))
        )
        customer_matched = bool(
            customer is not None and _line_has_ws_folded_phrase(line, customer)
        )
        if not name_matched and not customer_matched:
            continue
        if _line_is_employment_history_row(line, name_matched=name_matched):
            continue
        hit = (doc_id, page_no, line)
        if name_matched:
            named_hits.append(hit)
        if customer_matched:
            customer_hits.append(hit)

    def _unique(
        hits: list[tuple[str, int, str]],
    ) -> tuple[str, int, str] | None:
        uniq = list(dict.fromkeys(hits))
        if len(uniq) != 1:
            return None
        return uniq[0]

    if named_hits:
        return _unique(named_hits)
    if customer_hits:
        return _unique(customer_hits)
    return None


def _find_unique_semantic_project_exp_ref(
    project: ProjectCandidate,
    code: str,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
) -> SourceRef | None:
    """Fail-closed unique project-row match for semantic EXP quote backfill."""
    row = _find_unique_project_source_row(
        project,
        page_texts=page_texts,
        allowed_documents=allowed_documents,
        line_predicate=lambda line: _quote_supports_semantic_exp(code, line),
    )
    if row is None:
        return None
    doc_id, page_no, line = row
    quote = _extract_semantic_exp_quote(code, line)
    if not quote:
        return None
    return SourceRef(document_id=doc_id, page_no=page_no, quote_text=quote)


def _quote_supports_semantic_project_job(code: str, quote: str) -> bool:
    """True when quote supports a known PROJECT JOB semantic/literal rule."""
    text = (quote or "").strip()
    if not text:
        return False
    exp_alias = _JOB_SEMANTIC_EXP_ALIAS.get(code)
    if exp_alias is not None:
        return _quote_supports_semantic_exp(exp_alias, text)
    pattern = _JOB_LITERAL_TOKEN_PATTERNS.get(code)
    if pattern is None:
        return False
    return pattern.search(text) is not None


def _extract_semantic_project_job_quote(code: str, text: str) -> str | None:
    """Minimal actual substring in ``text`` that supports the JOB code."""
    exp_alias = _JOB_SEMANTIC_EXP_ALIAS.get(code)
    if exp_alias is not None:
        return _extract_semantic_exp_quote(exp_alias, text)
    pattern = _JOB_LITERAL_TOKEN_PATTERNS.get(code)
    if pattern is None:
        return None
    match = pattern.search(text)
    if match is None:
        return None
    return text[match.start() : match.end()]


def _find_unique_semantic_project_job_ref(
    project: ProjectCandidate,
    code: str,
    *,
    page_texts: dict[tuple[str, int], str],
    allowed_documents: dict[str, set[int]],
) -> SourceRef | None:
    """Fail-closed unique project-row match for semantic JOB quote backfill."""
    row = _find_unique_project_source_row(
        project,
        page_texts=page_texts,
        allowed_documents=allowed_documents,
        line_predicate=lambda line: _quote_supports_semantic_project_job(
            code, line
        ),
    )
    if row is None:
        return None
    doc_id, page_no, line = row
    quote = _extract_semantic_project_job_quote(code, line)
    if not quote:
        return None
    return SourceRef(document_id=doc_id, page_no=page_no, quote_text=quote)


def _biz_ref_is_same_project_row(
    project: ProjectCandidate, ref: SourceRef
) -> bool:
    """True when a quote is fail-closed same-project evidence for BIZ.

    Allows multi-line PPTX root spans (YY.MM ranges). Requires start-month
    match plus project_name or customer ws-fold match. Rejects employment-
    history section text. Does not accept same-page/same-year alone.
    """
    quote = (ref.quote_text or "").strip()
    if not quote:
        return False
    if not _PROJECT_ROW_RANGE_MARK_RE.search(quote):
        return False
    if _EMPLOYMENT_SECTION_RE.search(quote):
        return False
    if not _quote_matches_project_start_month(project, quote):
        return False
    name = (project.project_name or "").strip()
    customer = (project.customer_name or "").strip()
    name_ok = bool(name) and (
        name in quote or _line_has_ws_folded_phrase(quote, name)
    )
    cust_ok = bool(customer) and _line_has_ws_folded_phrase(quote, customer)
    return name_ok or cust_ok


def _find_reusable_project_root_ref_for_biz(
    project: ProjectCandidate,
) -> SourceRef | None:
    """First project.root source_ref that is fail-closed same-project evidence."""
    for ref in project.source_refs or []:
        if _biz_ref_is_same_project_row(project, ref):
            return ref
    return None


def backfill_project_biz_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v20: repair PROJECT business_domains refs from unique project source rows.

    Keeps existing BIZ codes/counts; only fills/replaces ``source_refs`` when
    fail-closed same-project evidence is found. Order:
    1) preserve existing valid BIZ refs
    2) reuse project.source_refs (multi-line PPTX roots allowed)
    3) unique page_texts line-level project row
    """
    if not candidate.projects:
        return candidate
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        new_biz: list[CodeRefCandidate] = []
        project_changed = False
        root_reuse = _find_reusable_project_root_ref_for_biz(project)
        for rel in project.business_domains:
            if any(
                _biz_ref_is_same_project_row(project, ref)
                for ref in (rel.source_refs or [])
            ):
                new_biz.append(rel)
                continue
            if root_reuse is not None:
                project_changed = True
                new_biz.append(
                    rel.model_copy(update={"source_refs": [root_reuse]})
                )
                continue
            if not page_texts:
                new_biz.append(rel)
                continue
            row = _find_unique_project_source_row(
                project,
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if row is None:
                new_biz.append(rel)
                continue
            doc_id, page_no, line = row
            match = SourceRef(
                document_id=doc_id, page_no=page_no, quote_text=line
            )
            project_changed = True
            new_biz.append(rel.model_copy(update={"source_refs": [match]}))
        if project_changed:
            changed = True
            new_projects.append(
                project.model_copy(update={"business_domains": new_biz})
            )
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


_PM_PL_TO_OPS_REPAIR_CODES = frozenset({"JOB-MGT-PM", "JOB-MGT-PL"})
# JOB codes checked by v20 PROJECT JOB semantic guard (PM/PL keep separate guard).
_JOB_SEMANTIC_GUARD_CODES = frozenset({"JOB-MGT", "JOB-OPS-SYS", "JOB-ARC-TA"})


def repair_project_pm_pl_to_ops_sys(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> tuple[ProfileCandidateDocument, int]:
    """v20: reclassify unsupported PROJECT PM/PL → JOB-OPS-SYS when OPS evidence exists.

    Applies only to existing ``project.jobs`` with code JOB-MGT-PM / JOB-MGT-PL
    that lack literal PM/PL tokens in ``source_refs``, when a fail-closed unique
    same-project source row strongly supports JOB-OPS-SYS. Sets
    ``code=JOB-OPS-SYS`` and ``source_refs=[semantic span]``. Does not create
    relations, increase relation count, or touch root jobs / EXP / TECH / BIZ /
    scalars. Literal PM/PL (boundary-safe; PMO ≠ PM) never repairs.
    """
    if not page_texts or not candidate.projects:
        return candidate, 0
    repaired = 0
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        new_jobs: list[CodeRefCandidate] = []
        project_changed = False
        for rel in project.jobs:
            code = (rel.code or "").strip()
            if code not in _PM_PL_TO_OPS_REPAIR_CODES:
                new_jobs.append(rel)
                continue
            if _project_job_has_literal_pm_pl_evidence(rel):
                new_jobs.append(rel)
                continue
            match = _find_unique_semantic_project_job_ref(
                project,
                "JOB-OPS-SYS",
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if match is None:
                new_jobs.append(rel)
                continue
            project_changed = True
            repaired += 1
            new_jobs.append(
                rel.model_copy(
                    update={"code": "JOB-OPS-SYS", "source_refs": [match]}
                )
            )
        if project_changed:
            changed = True
            new_projects.append(project.model_copy(update={"jobs": new_jobs}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate, 0
    return candidate.model_copy(update={"projects": new_projects}), repaired


def _project_job_has_literal_mgt_evidence(rel: CodeRefCandidate) -> bool:
    """True when any JOB-MGT quote contains literal 사업관리."""
    return any(
        _quote_supports_semantic_project_job("JOB-MGT", ref.quote_text or "")
        for ref in (rel.source_refs or [])
    )


def repair_project_mgt_to_ops_sys(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> tuple[ProfileCandidateDocument, int]:
    """v20: reclassify unsupported PROJECT JOB-MGT → JOB-OPS-SYS when OPS exists.

    Applies only to existing ``project.jobs`` with code JOB-MGT that lack
    literal ``사업관리`` in ``source_refs``, when a fail-closed unique
    same-project source row strongly supports JOB-OPS-SYS. Sets
    ``code=JOB-OPS-SYS`` and ``source_refs=[semantic span]``. Does not create
    relations or touch root jobs / EXP / TECH / BIZ / scalars. Literal
    사업관리 never repairs (preserve JOB-MGT).
    """
    if not page_texts or not candidate.projects:
        return candidate, 0
    repaired = 0
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        new_jobs: list[CodeRefCandidate] = []
        project_changed = False
        for rel in project.jobs:
            code = (rel.code or "").strip()
            if code != "JOB-MGT":
                new_jobs.append(rel)
                continue
            if _project_job_has_literal_mgt_evidence(rel):
                new_jobs.append(rel)
                continue
            match = _find_unique_semantic_project_job_ref(
                project,
                "JOB-OPS-SYS",
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if match is None:
                new_jobs.append(rel)
                continue
            project_changed = True
            repaired += 1
            new_jobs.append(
                rel.model_copy(
                    update={"code": "JOB-OPS-SYS", "source_refs": [match]}
                )
            )
        if project_changed:
            changed = True
            new_projects.append(project.model_copy(update={"jobs": new_jobs}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate, 0
    return candidate.model_copy(update={"projects": new_projects}), repaired


def _project_job_has_semantic_guard_evidence(rel: CodeRefCandidate) -> bool:
    """True when job is out of guard scope or has supporting semantic/literal quote."""
    code = (rel.code or "").strip()
    if code not in _JOB_SEMANTIC_GUARD_CODES:
        return True
    return any(
        _quote_supports_semantic_project_job(code, ref.quote_text or "")
        for ref in (rel.source_refs or [])
    )


def drop_project_jobs_without_semantic_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: drop PROJECT JOB-MGT/OPS-SYS/TA lacking semantic/literal quotes.

    JOB-MGT needs literal 사업관리; JOB-OPS-SYS needs strong OPS phrases;
    JOB-ARC-TA needs boundary-safe TA. JOB-MGT-PM/PL and other JOB codes are
    left for their dedicated guards / unchanged. Root jobs stay.
    """
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        kept_jobs = [
            job
            for job in project.jobs
            if _project_job_has_semantic_guard_evidence(job)
        ]
        if len(kept_jobs) != len(project.jobs):
            changed = True
            new_projects.append(project.model_copy(update={"jobs": kept_jobs}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


def backfill_semantic_project_job_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v20: repair PROJECT JOB refs from a unique semantic project source row.

    Only existing ``project.jobs`` for known JOB semantic codes (OPS-SYS, MGT,
    TA, PM, PL). Does not create/reclassify JOB codes. Leaves already-supporting
    refs unchanged. Never modifies root jobs, EXP/TECH/BIZ, or project scalars.
    """
    if not page_texts or not candidate.projects:
        return candidate
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        new_jobs: list[CodeRefCandidate] = []
        project_changed = False
        for rel in project.jobs:
            code = (rel.code or "").strip()
            if code not in _JOB_SEMANTIC_BACKFILL_CODES:
                new_jobs.append(rel)
                continue
            if any(
                _quote_supports_semantic_project_job(code, ref.quote_text or "")
                for ref in (rel.source_refs or [])
            ):
                new_jobs.append(rel)
                continue
            match = _find_unique_semantic_project_job_ref(
                project,
                code,
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if match is None:
                new_jobs.append(rel)
                continue
            project_changed = True
            new_jobs.append(rel.model_copy(update={"source_refs": [match]}))
        if project_changed:
            changed = True
            new_projects.append(project.model_copy(update={"jobs": new_jobs}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


def backfill_semantic_project_expertise_evidence(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: dict[tuple[str, int], str] | None,
    allowed_documents: dict[str, set[int]],
) -> ProfileCandidateDocument:
    """v20: repair PROJECT EXP refs from a unique semantic project source row.

    Only existing ``project.expertise`` relations for known EXP codes. Does not
    create new EXP codes. Leaves already-supporting refs unchanged. Never
    modifies jobs/skills/domains/root expertise or project scalars.
    """
    if not page_texts or not candidate.projects:
        return candidate
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        new_exp: list[CodeRefCandidate] = []
        project_changed = False
        for rel in project.expertise:
            code = (rel.code or "").strip()
            if code not in _EXP_SEMANTIC_PATTERNS:
                new_exp.append(rel)
                continue
            if any(
                _quote_supports_semantic_exp(code, ref.quote_text or "")
                for ref in (rel.source_refs or [])
            ):
                new_exp.append(rel)
                continue
            match = _find_unique_semantic_project_exp_ref(
                project,
                code,
                page_texts=page_texts,
                allowed_documents=allowed_documents,
            )
            if match is None:
                new_exp.append(rel)
                continue
            project_changed = True
            new_exp.append(rel.model_copy(update={"source_refs": [match]}))
        if project_changed:
            changed = True
            new_projects.append(project.model_copy(update={"expertise": new_exp}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


def drop_project_expertise_without_semantic_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: drop PROJECT expertise whose quotes do not support known EXP codes.

    Only ``project.expertise`` for EXP-INFRA/MGT/SEC-OPS/SEC-BUILD. Unknown EXP
    codes, root expertise, jobs/skills/domains, and the project row stay.
    Any one supporting quote keeps the relation.
    """
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        kept_exp: list[CodeRefCandidate] = []
        dropped = False
        for rel in project.expertise:
            code = (rel.code or "").strip()
            if code not in _EXP_SEMANTIC_PATTERNS:
                kept_exp.append(rel)
                continue
            if any(
                _quote_supports_semantic_exp(code, ref.quote_text or "")
                for ref in (rel.source_refs or [])
            ):
                kept_exp.append(rel)
                continue
            dropped = True
        if dropped:
            changed = True
            new_projects.append(project.model_copy(update={"expertise": kept_exp}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


def drop_project_jobs_without_literal_pm_pl_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v20: drop PROJECT JOB-MGT-PM/PL lacking literal PM/PL quote tokens.

    Only project ``jobs`` relations are filtered. Root jobs, other JOB codes
    (e.g. JOB-ARC-TA), skills/expertise/domains, and the project itself stay.
    """
    changed = False
    new_projects: list[ProjectCandidate] = []
    for project in candidate.projects:
        kept_jobs = [
            job
            for job in project.jobs
            if _project_job_has_literal_pm_pl_evidence(job)
        ]
        if len(kept_jobs) != len(project.jobs):
            changed = True
            new_projects.append(project.model_copy(update={"jobs": kept_jobs}))
        else:
            new_projects.append(project)
    if not changed:
        return candidate
    return candidate.model_copy(update={"projects": new_projects})


def apply_normalized_quote_evidence(
    candidate: ProfileCandidateDocument,
) -> ProfileCandidateDocument:
    """v10 post-normalize filter: require non-empty quote_text evidence.

    After ``normalize_candidate`` may discard invalid document/page refs or
    clear invalid quotes to ``None``:
    - drop projects with no retained source_ref that still has quote_text
    - drop project jobs/skills/expertise lacking quoted source_refs
    - BIZ / CUSTOMER_TYPE relations are unchanged
    """
    kept: list[ProjectCandidate] = []
    for project in candidate.projects:
        if not any(_source_ref_has_quote(ref) for ref in (project.source_refs or [])):
            continue
        kept.append(
            project.model_copy(
                update={
                    "jobs": [
                        rel
                        for rel in project.jobs
                        if _relation_has_quote_evidence(rel)
                    ],
                    "skills": [
                        rel
                        for rel in project.skills
                        if _relation_has_quote_evidence(rel)
                    ],
                    "expertise": [
                        rel
                        for rel in project.expertise
                        if _relation_has_quote_evidence(rel)
                    ],
                }
            )
        )
    if len(kept) == len(candidate.projects) and all(
        len(kept[i].jobs) == len(candidate.projects[i].jobs)
        and len(kept[i].skills) == len(candidate.projects[i].skills)
        and len(kept[i].expertise) == len(candidate.projects[i].expertise)
        for i in range(len(kept))
    ):
        return candidate
    return candidate.model_copy(update={"projects": kept})


def _project_names_identity_match(a: ProjectCandidate, b: ProjectCandidate) -> bool:
    na = (a.project_name or "").strip()
    nb = (b.project_name or "").strip()
    if not na or not nb:
        return False
    return na == nb or _fold_ws_text(na) == _fold_ws_text(nb)


def _project_customers_identity_match(a: ProjectCandidate, b: ProjectCandidate) -> bool:
    ca = (a.customer_name or "").strip()
    cb = (b.customer_name or "").strip()
    if not ca or not cb:
        return False
    return _fold_ws_text(ca) == _fold_ws_text(cb)


def _project_date_parts_local(
    value: Any,
) -> tuple[int, int | None, int | None] | None:
    """Parse PROJECT dates (YYYY.MM / YYYY-MM / YYYY.MM.DD / YYYY-MM-DD).

    Local minimal helper — does not import diff_engine private parsers.
    Year-only is rejected for identity (month precision required later).
    """
    text = _optional_text(value)
    if text is None:
        return None
    folded = text.split("T", 1)[0] if "T" in text else text
    folded = folded.replace(".", "-").replace("/", "-").strip()
    parts = folded.split("-")
    if not parts or not parts[0].isdigit() or len(parts[0]) != 4:
        return None
    year = int(parts[0])
    if year < 1900 or year > 2100:
        return None
    month: int | None = None
    day: int | None = None
    if len(parts) >= 2 and parts[1].isdigit():
        month = int(parts[1])
        if month < 1 or month > 12:
            return None
    if len(parts) >= 3 and parts[2].isdigit():
        day = int(parts[2])
        if day < 1 or day > 31:
            return None
    if day is not None and month is None:
        return None
    return year, month, day


def _project_dates_prefix_compatible(a: Any, b: Any) -> bool:
    """Symmetric PROJECT date compatibility (no ±1 month, no year-only).

    Month precision vs day-in-same-month is compatible in either direction.
    Both sides need at least year+month.
    """
    if _optional_text(a) is None or _optional_text(b) is None:
        return False
    pa = _project_date_parts_local(a)
    pb = _project_date_parts_local(b)
    if pa is None or pb is None:
        return False
    ya, ma, da = pa
    yb, mb, db = pb
    if ma is None or mb is None:
        return False
    if ya != yb or ma != mb:
        return False
    if da is not None and db is not None and da != db:
        return False
    return True


def _project_period_compatible(a: ProjectCandidate, b: ProjectCandidate) -> bool:
    """Reuse PROJECT date precision policy (prefix-compatible; no ±1 month)."""
    return _project_dates_prefix_compatible(
        a.start_date, b.start_date
    ) and _project_dates_prefix_compatible(a.end_date, b.end_date)


def _project_ym_ordinal(value: Any) -> int | None:
    """Month ordinal for overlap; requires year+month precision."""
    parts = _project_date_parts_local(value)
    if parts is None or parts[1] is None:
        return None
    year, month, _day = parts
    return year * 12 + (month - 1)


def _project_periods_month_overlap(
    a: ProjectCandidate, b: ProjectCandidate
) -> bool:
    """Inclusive month-range overlap; missing/invalid bounds → False."""
    sa = _project_ym_ordinal(a.start_date)
    ea = _project_ym_ordinal(a.end_date)
    sb = _project_ym_ordinal(b.start_date)
    eb = _project_ym_ordinal(b.end_date)
    if sa is None or ea is None or sb is None or eb is None:
        return False
    if ea < sa or eb < sb:
        return False
    return max(sa, sb) <= min(ea, eb)


def _project_name_customer_start_overlap_match(
    a: ProjectCandidate, b: ProjectCandidate
) -> bool:
    """Fail-closed second-pass duplicate when end months disagree but periods overlap.

    Requires same name (exact/ws-fold), both customers non-empty + ws-fold equal,
    start month-precision compatible, and month-unit period overlap.
    """
    return (
        _project_names_identity_match(a, b)
        and _project_customers_identity_match(a, b)
        and _project_dates_prefix_compatible(a.start_date, b.start_date)
        and _project_periods_month_overlap(a, b)
    )


def _project_root_document_ids(project: ProjectCandidate) -> set[str]:
    """Document ids from root source_refs only (no relation dig)."""
    out: set[str] = set()
    for ref in project.source_refs or []:
        doc_id = (getattr(ref, "document_id", None) or "").strip()
        if doc_id:
            out.add(doc_id)
    return out


def _projects_are_cross_document(
    a: ProjectCandidate, b: ProjectCandidate
) -> bool:
    """True when both have root doc ids and the sets are disjoint."""
    da = _project_root_document_ids(a)
    db = _project_root_document_ids(b)
    if not da or not db:
        return False
    return da.isdisjoint(db)


_PROJECT_NAME_YEAR_RE = re.compile(r"(?:19|20)\d{2}년?")
# Longer generics first so replacements do not leave shorter fragments.
_PROJECT_NAME_GENERIC_FRAGMENTS: tuple[str, ...] = (
    "유지보수",
    "프로젝트",
    "업무분석",
    "업무이관",
    "사업",
    "구축",
    "운영",
)


def _fold_project_name_lex(value: str | None) -> str:
    """NFKC/casefold name with whitespace and punctuation removed."""
    text = unicodedata.normalize("NFKC", value or "").casefold()
    chars: list[str] = []
    for ch in text:
        if ch.isspace():
            continue
        if unicodedata.category(ch).startswith("P"):
            continue
        chars.append(ch)
    return "".join(chars)


def _strip_weak_project_name_parts(folded: str) -> str:
    """Drop year tokens and generic activity fragments for anchor checks."""
    text = _PROJECT_NAME_YEAR_RE.sub("", folded)
    for frag in _PROJECT_NAME_GENERIC_FRAGMENTS:
        text = text.replace(frag, "")
    return text


def _longest_common_substring_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    # Bound DP to short project-name strings.
    prev = [0] * (len(b) + 1)
    best = 0
    for ch_a in a:
        cur = [0] * (len(b) + 1)
        for j, ch_b in enumerate(b, start=1):
            if ch_a == ch_b:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _project_names_have_strong_lexical_anchor(
    a: ProjectCandidate, b: ProjectCandidate
) -> bool:
    """Distinctive name overlap; years/generic verbs alone never suffice.

    Fail-open (False) when overlap is weak — prefer appending over false dedupe.
    """
    fa = _fold_project_name_lex(a.project_name)
    fb = _fold_project_name_lex(b.project_name)
    if not fa or not fb:
        return False
    if _longest_common_substring_len(fa, fb) < 6:
        return False
    sa = _strip_weak_project_name_parts(fa)
    sb = _strip_weak_project_name_parts(fb)
    if not sa or not sb:
        return False
    return _longest_common_substring_len(sa, sb) >= 4


def _second_is_aggregate_of_first_segments(
    first_projects: list[ProjectCandidate], second: ProjectCandidate
) -> bool:
    """Suppress second when it is a cross-doc aggregate of contiguous first segments."""
    second_start = _project_ym_ordinal(second.start_date)
    second_end = _project_ym_ordinal(second.end_date)
    if (
        second_start is None
        or second_end is None
        or second_end < second_start
    ):
        return False

    eligible: list[tuple[int, int]] = []
    for first in first_projects:
        if not _project_customers_identity_match(first, second):
            continue
        if not _projects_are_cross_document(first, second):
            continue
        if not _project_names_have_strong_lexical_anchor(first, second):
            continue
        fs = _project_ym_ordinal(first.start_date)
        fe = _project_ym_ordinal(first.end_date)
        if fs is None or fe is None or fe < fs:
            continue
        if fs < second_start or fe > second_end:
            continue
        eligible.append((fs, fe))

    if len(eligible) < 2:
        return False

    eligible.sort(key=lambda item: (item[0], item[1]))
    # Find a contiguous chain of >=2 periods whose union equals second span.
    n = len(eligible)
    for i in range(n):
        chain_max_end = eligible[i][1]
        for j in range(i + 1, n):
            next_start, next_end = eligible[j]
            prev_end = chain_max_end
            if next_start > prev_end + 1:
                break
            chain_max_end = max(chain_max_end, next_end)
            if (
                eligible[i][0] == second_start
                and chain_max_end == second_end
            ):
                return True
    return False


def _second_is_near_period_title_variant(
    first: ProjectCandidate, second: ProjectCandidate
) -> bool:
    """Suppress cross-doc same-customer title variants with near start/same end."""
    if not _project_customers_identity_match(first, second):
        return False
    if not _projects_are_cross_document(first, second):
        return False
    if not _project_names_have_strong_lexical_anchor(first, second):
        return False
    fs = _project_ym_ordinal(first.start_date)
    fe = _project_ym_ordinal(first.end_date)
    ss = _project_ym_ordinal(second.start_date)
    se = _project_ym_ordinal(second.end_date)
    if fs is None or fe is None or ss is None or se is None:
        return False
    if fe != se:
        return False
    if abs(fs - ss) > 1:
        return False
    return _project_periods_month_overlap(first, second)


def _second_project_matches_first(
    first_projects: list[ProjectCandidate], second: ProjectCandidate
) -> bool:
    """True when second should not be appended (same identity or ambiguous).

    Fail-closed: name+compatible dates match, or unique customer+compatible
    dates match, or ambiguous multi customer+date hits → treat as covered /
    non-supplement (never invent a duplicate). Also name+customer+compatible
    start with month-period overlap (end may differ). Cross-document aggregate
    spans and near-period title variants are also suppressed.
    """
    for first in first_projects:
        if _project_names_identity_match(first, second) and _project_period_compatible(
            first, second
        ):
            return True
        if _project_name_customer_start_overlap_match(first, second):
            return True
        if _second_is_near_period_title_variant(first, second):
            return True
    if _second_is_aggregate_of_first_segments(first_projects, second):
        return True
    customer_hits = [
        first
        for first in first_projects
        if _project_customers_identity_match(first, second)
        and _project_period_compatible(first, second)
    ]
    # Unique customer hit → same project. Ambiguous (>1) → do not supplement.
    return len(customer_hits) >= 1


def supplement_projects_from_second_pass(
    first: ProfileCandidateDocument,
    second: ProfileCandidateDocument,
) -> tuple[ProfileCandidateDocument, int]:
    """Append unmatched second-pass projects; first projects/relations win.

    Does not create source-only projects, does not merge scalars/relations for
    matched identities, and never removes/overwrites first projects.
    """
    first_projects = list(first.projects)
    extras: list[ProjectCandidate] = []
    for cand in second.projects:
        # Compare against first + already-accepted extras so second-pass
        # internal duplicates are not appended twice.
        if _second_project_matches_first(first_projects + extras, cand):
            continue
        extras.append(cand)
    if not extras:
        return first, 0
    return (
        first.model_copy(update={"projects": first_projects + extras}),
        len(extras),
    )
