"""Pure helpers: original-document grounding for Diff review decisions."""

from __future__ import annotations

import re
from typing import Any

_ROOT_JOB_PATH_RE = re.compile(r"^jobs\[\d+\]$")
_ROOT_EXP_PATH_RE = re.compile(r"^expertise\[\d+\]$")


def iter_new_value_source_refs(new_value: Any) -> list[dict[str, Any]]:
    if not isinstance(new_value, dict):
        return []
    refs = new_value.get("source_refs")
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, dict)]


def source_ref_has_original_grounding(ref: dict[str, Any]) -> bool:
    """True only with real 원문 quote — bare metadata does not count."""
    quote = str(ref.get("quote_text") or "").strip()
    return bool(quote)


def evidence_item_has_original_grounding(item: Any) -> bool:
    """Accept linked Evidence rows (id) or items that still carry quote text."""
    if item is None:
        return False
    if isinstance(item, dict):
        if item.get("id") is not None:
            return True
        return bool(str(item.get("quote_text") or "").strip())
    if getattr(item, "id", None) is not None:
        return True
    return bool(str(getattr(item, "quote_text", None) or "").strip())


def diff_has_original_evidence(
    *,
    new_value: Any = None,
    evidence: list[Any] | None = None,
) -> bool:
    """True when source_refs and/or linked Evidence carry original grounding.

    Does not treat empty ``source_refs``, ``evidence_type``, or confidence as
    proof of 원문 근거. Project relation refs are not consulted here — callers
    must pass the Diff's own ``new_value`` / ``evidence`` only.
    """
    for ref in iter_new_value_source_refs(new_value):
        if source_ref_has_original_grounding(ref):
            return True
    for item in evidence or []:
        if evidence_item_has_original_grounding(item):
            return True
    return False


def is_root_job_or_exp_new(
    *,
    entity_type: str | None,
    change_type: str | None,
    field_name: str | None = None,
    candidate_path: str | None = None,
) -> bool:
    """Root JOB/EXP NEW only — not PROJECT relation rows."""
    if (change_type or "").strip().upper() != "NEW":
        return False
    if field_name not in (None, ""):
        return False
    entity = (entity_type or "").strip().upper()
    path = candidate_path or ""
    if entity == "JOB":
        return bool(_ROOT_JOB_PATH_RE.fullmatch(path))
    if entity == "EXP":
        return bool(_ROOT_EXP_PATH_RE.fullmatch(path))
    return False


def is_ungrounded_root_job_or_exp_new(
    *,
    entity_type: str | None,
    change_type: str | None,
    field_name: str | None = None,
    candidate_path: str | None = None,
    new_value: Any = None,
    evidence: list[Any] | None = None,
) -> bool:
    if not is_root_job_or_exp_new(
        entity_type=entity_type,
        change_type=change_type,
        field_name=field_name,
        candidate_path=candidate_path,
    ):
        return False
    return not diff_has_original_evidence(new_value=new_value, evidence=evidence)
