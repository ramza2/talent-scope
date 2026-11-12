"""Project-row hints for an opt-in, read-only v29-v30 LLM comparison.

Never changes original [DOCUMENT]/[PAGE] evidence; only builds extra
untrusted project grouping context for PROJECTS prompts.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable


_DOC_RE = re.compile(r"(?m)^\[DOCUMENT (D[1-9]\d*)\]\s*$")
_PAGE_RE = re.compile(r"(?m)^\[PAGE (\d+)\]\s*$")


def prompt_source_slots(document_blocks: str, alias_to_id: dict[str, str]) -> set[tuple[str, int]]:
    """Find ONLY document/page markers within the actual LLM source chunk."""
    markers = list(_DOC_RE.finditer(document_blocks or ""))
    result: set[tuple[str, int]] = set()
    for index, match in enumerate(markers):
        doc_id = alias_to_id.get(match.group(1))
        if not doc_id:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(document_blocks)
        section = document_blocks[match.end():end]
        result.update((doc_id, int(page)) for page in _PAGE_RE.findall(section))
    return result


def scoped_group_data(
    groups: list[dict],
    *,
    document_blocks: str,
    alias_to_id: dict[str, str],
) -> list[dict]:
    """Only include hints whose actual supporting page is in this source chunk."""
    slots = prompt_source_slots(document_blocks, alias_to_id)
    id_to_alias = {doc_id: alias for alias, doc_id in alias_to_id.items()}
    output: list[dict] = []
    for group in groups:
        if group.get("status") not in {"verified_exact_multi_document", "single_source_unmatched"}:
            continue
        sources = [
            source for source in group.get("sources", [])
            if (str(source.get("document_id")), source.get("page_no")) in slots
        ]
        if not sources:
            continue
        chosen = sources[0]
        names = list(dict.fromkeys(
            str(source.get("project_name") or "") for source in sources if source.get("project_name")
        ))
        output.append({
            "id": group["group_id"],
            "status": "verified_cross_document" if group["status"] == "verified_exact_multi_document"
                      else "needs_review_no_merge",
            "months": group["period_months"],
            "names": names or group.get("project_names", [])[:1],
            "customer": chosen.get("customer") or "",
            "role": chosen.get("role") or "",
            "pages": [
                {"d": id_to_alias[str(s["document_id"])], "p": s["page_no"]}
                for s in sources
            ],
        })
    return output


def add_group_hints(
    original_user_prompt: str,
    *,
    document_blocks: str,
    groups: list[dict],
    alias_to_id: dict[str, str],
    max_hint_chars: int = 12000,
    audit: Callable[[int, int], None] | None = None,
) -> str:
    """Append an explicitly untrusted, page-scoped JSON hint to a user prompt.

    Missing page scope means no hints. Reject overbudget hints rather than
    truncating a grouping or changing the original document input.
    """
    items = scoped_group_data(
        groups, document_blocks=document_blocks, alias_to_id=alias_to_id
    )
    if audit:
        audit(len(items), 0)
    if not items:
        return original_user_prompt
    payload = {
        "kind": "project_row_geometry_hints_v30_experiment",
        "rules": (
            "Untrusted row associations only. Never use these hints as literal "
            "quote evidence. All source_refs must cite exact text from the "
            "[DOCUMENT] [PAGE] source above. Do not fabricate, change, or "
            "combine the fields of neighboring rows. Groups marked needs_review "
            "are distinct, not automatically merged."
        ),
        "groups": items,
    }
    hint = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(hint) > max_hint_chars:
        raise ValueError(f"SCOPED_GROUP_HINTS_EXCEED_BUDGET:{len(hint)}>{max_hint_chars}")
    if audit:
        audit(len(items), len(hint))
    return (
        original_user_prompt
        + "\n===== BEGIN UNTRUSTED PROJECT ROW GROUPING HINTS =====\n"
        + hint
        + "\n===== END UNTRUSTED PROJECT ROW GROUPING HINTS =====\n"
    )


def validate_cluster_report(report: dict, *, run_id: str, candidate_sha: str,
                            known_documents: set[str]) -> list[dict]:
    if report.get("run_id") != run_id or report.get("candidate_sha256_unchanged") != candidate_sha:
        raise ValueError("GROUP_HINTS_RUN_OR_CANDIDATE_MISMATCH")
    if report.get("mode") != "readonly_v30_row_cluster_preview":
        raise ValueError("GROUP_HINTS_INVALID_MODE")
    groups = report.get("groups")
    if not isinstance(groups, list):
        raise ValueError("GROUP_HINTS_MISSING_GROUPS")
    if len(groups) != report.get("group_count"):
        raise ValueError("GROUP_HINTS_COUNT_MISMATCH")
    if sum(g.get("status") == "verified_exact_multi_document" for g in groups) != report.get(
        "safe_multi_document_group_count"
    ):
        raise ValueError("GROUP_HINTS_SAFE_MERGE_COUNT_MISMATCH")
    known_statuses = {"verified_exact_multi_document", "single_source_unmatched"}
    for group in groups:
        if group.get("status") not in known_statuses:
            raise ValueError("GROUP_HINTS_AMBIGUOUS_UNVERIFIED_ROW")
        if not group.get("group_id") or not isinstance(group.get("sources"), list):
            raise ValueError("GROUP_HINTS_MISSING_ROW_PROVENANCE")
        if not group["sources"]:
            raise ValueError("GROUP_HINTS_EMPTY_ROW_PROVENANCE")
        for source in group["sources"]:
            if str(source.get("document_id")) not in known_documents:
                raise ValueError("GROUP_HINTS_UNKNOWN_DOCUMENT")
            if not isinstance(source.get("page_no"), int) or source["page_no"] < 1:
                raise ValueError("GROUP_HINTS_INVALID_PAGE")
            if not source.get("row_id") or not source.get("project_name"):
                raise ValueError("GROUP_HINTS_MISSING_PROJECT_IDENTITY")
    return groups
