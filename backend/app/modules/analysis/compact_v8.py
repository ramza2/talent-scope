"""v8 compact intermediate protocol adapters (aliases + expand → candidate-v1)."""

from __future__ import annotations

import re
from typing import Any

from app.ai.schemas.profile_candidate import (
    PROFILE_SCALAR_FIELD_NAMES,
    SCHEMA_VERSION,
    CodeRefCandidate,
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
    page_no = item.get("p", item.get("page_no"))
    quote = _optional_text(item.get("q", item.get("quote_text")))
    out: dict[str, Any] = {"document_id": doc_id}
    if page_no is not None:
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
    if item.get("rep") is not None:
        out["is_representative"] = item["rep"]
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
