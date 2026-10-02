"""v8 compact intermediate protocol adapters (aliases + expand → candidate-v1)."""

from __future__ import annotations

import re
from typing import Any

from app.ai.schemas.profile_candidate import (
    PROFILE_SCALAR_FIELD_NAMES,
    SCHEMA_VERSION,
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
            profile[field] = value
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
    if item.get("y") is not None:
        out["last_used_year"] = item["y"]
    if item.get("m") is not None:
        out["experience_months"] = item["m"]
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
    text_mapping = {
        "n": "certification_name",
        "is": "issuer",
        "ad": "acquired_date",
        "ex": "expiry_date",
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
) -> list[dict[str, Any]]:
    """Prefer relation-specific compact map; else project-level refs."""
    if isinstance(relation_map, dict):
        bucket = relation_map.get(relation_key)
        if isinstance(bucket, dict) and code in bucket:
            return expand_compact_refs(bucket[code], alias_to_id)
        if isinstance(bucket, list):
            # Shared refs for the whole relation list.
            return expand_compact_refs(bucket, alias_to_id)
    return list(project_refs)


def _expand_code_array(
    codes: Any,
    *,
    project_refs: list[dict[str, Any]],
    relation_map: dict[str, Any] | None,
    relation_key: str,
    alias_to_id: dict[str, str],
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
            )
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
                )
            entry: dict[str, Any] = {"code": code_s}
            raw_value = _optional_text(item.get("v"))
            if raw_value is None:
                raw_value = _optional_text(item.get("raw_value"))
            if raw_value is not None:
                entry["raw_value"] = raw_value
            if refs:
                entry["source_refs"] = refs
            out.append(entry)
    return out


def _expand_project(
    item: Any, alias_to_id: dict[str, str]
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    project_refs = expand_compact_refs(item.get("r"), alias_to_id)
    relation_map = item.get("rm") if isinstance(item.get("rm"), dict) else None

    out: dict[str, Any] = {}
    text_mapping = {
        "n": "project_name",
        "cu": "customer_name",
        "s": "start_date",
        "e": "end_date",
        "resp": "responsibilities",
        "sum": "project_summary",
    }
    for short, long in text_mapping.items():
        _set_optional_text(out, long, item.get(short))
    # v9: mo = duration_months. Legacy v8: d only when a valid integer.
    # Never treat root d="D1" as a source ref (refs come from r only).
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
) -> dict[str, Any]:
    """Expand compact PROJECTS JSON into a projects-only candidate-v1 dict."""
    projects_raw = raw.get("pr")
    if projects_raw is None:
        projects_raw = raw.get("projects")
    projects = _map_list(projects_raw, _expand_project, alias_to_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "projects": projects,
    }
