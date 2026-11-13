"""Direct native-PDF-row -> v30 Candidate/Diff (pure, opt-in runtime/preview).

No DB/LLM/MinIO calls here. The calling *read-only* CLI supplies verified
native rows, a historical v25 candidate and the latest confirmed snapshot.

Every well-formed native source group is one independent project candidate;
names that merely LOOK similar are never merged. Unverified source rows
produce a hard error rather than silently disappearing. If no native rows
were found, fail rather than present a misleading partial Source-First pass.

Native role cells are kept verbatim (as user-facing responsibilities), not
inferred taxonomy job codes. Optional AI source_refs must already exist,
be owned by the native group, and be literal excerpts from stored page text.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument, ProjectCandidate, SourceRef,
)
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.diff_engine import DiffSpec
from app.modules.analysis.project_row_match_v30 import build_clusters
from app.modules.analysis.project_source_first_diff_guard_v30 import (
    build_source_first_review_safe_diffs,
)
import unicodedata

PROJECT_PATH = re.compile(r"^projects\\[(\\d+)\\](?:\\.|$)")


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _ym(value: object, *, allow_ongoing: bool = False) -> str | None:
    raw = str(value or "").strip()
    if allow_ongoing and raw.casefold() in {"현재", "진행중", "재직중", "present"}:
        return "현재"
    match = re.fullmatch(
        r"((?:19|20)\\d{2})[.\\-/](0?[1-9]|1[0-2])(?:[.\\-/]\\d{1,2})?",
        raw,
    )
    return f"{match[1]}.{int(match[2]):02d}" if match else None

GOOD_STATUS = {
    "verified_exact_multi_document",
    "single_source_unmatched",
    "ambiguous_same_document_key",
}


@dataclass
class NativeDirectPreview:
    candidate: ProfileCandidateDocument
    specs: list[DiffSpec]
    diagnostics: dict[str, Any]
    # Sensitive data must only be written to explicitly chosen local 0600 file.
    private_source_provenance: list[dict[str, Any]]


def assemble_native_direct(
    *,
    native_rows: list[dict[str, Any]],
    original_candidate_json: dict[str, Any],
    latest_confirmed_snapshot: dict[str, Any],
    page_texts: dict[tuple[str, int], str] | None = None,
) -> NativeDirectPreview:
    if not isinstance(native_rows, list) or not native_rows:
        raise ValueError("NATIVE_V30_NO_ROWS")
    if (
        not isinstance(original_candidate_json, dict)
        or not isinstance(latest_confirmed_snapshot, dict)
        or not isinstance(latest_confirmed_snapshot.get("projects"), list)
    ):
        raise ValueError("NATIVE_V30_CORE_OR_SNAPSHOT_INVALID")
    original = ProfileCandidateDocument.model_validate(original_candidate_json)
    clusters = build_clusters(native_rows)
    groups = clusters.get("groups") or []
    if (
        not groups or clusters.get("group_count") != len(groups)
        or sum(g.get("status") not in GOOD_STATUS for g in groups)
    ):
        # Report the input row count, not patient names or PDF text.
        raise ValueError("NATIVE_V30_UNVERIFIED_OR_AMBIGUOUS_SOURCE_ROWS")

    allowed_pages = page_texts or {}
    if not isinstance(allowed_pages, dict):
        raise ValueError("NATIVE_V30_PAGE_TEXT_MAP_INVALID")

    # Reverse index prevents a single LLM candidate from lending its
    # provenance to multiple distinct native PDF groups.
    ai_matches: dict[int, list[int]] = defaultdict(list)
    group_ai: list[list[int]] = []
    for idx, group in enumerate(groups):
        aliases = {_fold(v) for v in group.get("project_names") or [] if v}
        customers = {_fold(v) for v in group.get("customers") or [] if v}
        period = group.get("period_months")
        if (
            not aliases or not customers or len(period or []) != 2
            or not isinstance(group.get("group_id"), str)
            or not group["group_id"]
        ):
            raise ValueError(f"NATIVE_V30_GROUP_IDENTITY_INVALID:{idx}")
        hits: list[int] = []
        for ai_idx, row in enumerate(original.projects):
            if (
                _fold(row.project_name) in aliases
                and _fold(row.customer_name) in customers
                and _ym(row.start_date) == period[0]
                and _ym(row.end_date, allow_ongoing=True) == period[1]
            ):
                hits.append(ai_idx)
                ai_matches[ai_idx].append(idx)
        group_ai.append(hits)

    projects: list[ProjectCandidate] = []
    private_sources: list[dict[str, Any]] = []
    evidence_bound_count = 0
    source_role_count = 0
    preserved_quote_ref_count = 0
    seen_group_ids: set[str] = set()
    for idx, group in enumerate(groups):
        group_id = group["group_id"]
        if group_id in seen_group_ids:
            raise ValueError(f"NATIVE_V30_DUPLICATE_GROUP_ID:{idx}")
        seen_group_ids.add(group_id)
        sources = group.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"NATIVE_V30_NO_NATIVE_PROVENANCE:{idx}")
        source_pages = set()
        for src in sources:
            bbox = src.get("bbox") if isinstance(src, dict) else None
            if (
                not isinstance(src, dict)
                or not isinstance(src.get("document_id"), str)
                or not src["document_id"]
                or type(src.get("page_no")) is not int
                or src["page_no"] < 1
                or not isinstance(src.get("row_id"), str) or not src["row_id"]
                or not isinstance(bbox, list) or len(bbox) != 4
                or not all(type(x) in (int, float) for x in bbox)
                or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]
            ):
                raise ValueError(f"NATIVE_V30_INVALID_ROW_GEOMETRY:{idx}")
            source_pages.add((src["document_id"], src["page_no"]))

        titles = group["project_names"]
        customers = group["customers"]
        roles = group.get("roles") or []
        if (
            not isinstance(roles, list)
            or any(not isinstance(x, str) or not x.strip()
                   or len(x) > 500 for x in roles)
        ):
            raise ValueError(f"NATIVE_V30_ROLE_CELL_INVALID:{idx}")
        responsibilities = "수행역할: " + " / ".join(roles) if roles else None
        if responsibilities and len(responsibilities) > 2000:
            raise ValueError(f"NATIVE_V30_ROLE_CELL_TOO_LONG:{idx}")
        if responsibilities:
            source_role_count += 1

        refs: list[SourceRef] = []
        hits = group_ai[idx]
        # A single AI candidate may enrich a group ONLY if that candidate
        # matches no other groups and its original quote is still present.
        if len(hits) == 1 and ai_matches[hits[0]] == [idx]:
            old_refs = original.projects[hits[0]].source_refs
            if old_refs:
                checked: list[SourceRef] = []
                for ref in old_refs:
                    key = (str(ref.document_id), ref.page_no)
                    quote = ref.quote_text or ""
                    original_page = allowed_pages.get(key)
                    if (
                        key not in source_pages
                        or not isinstance(original_page, str)
                        or not quote or len(quote) > 500
                        or quote not in original_page
                    ):
                        checked = []
                        break
                    checked.append(SourceRef(
                        document_id=str(ref.document_id),
                        page_no=ref.page_no,
                        quote_text=quote,
                    ))
                if checked:
                    refs = checked
                    evidence_bound_count += 1
                    preserved_quote_ref_count += len(refs)

        project = ProjectCandidate(
            project_name=titles[0],
            customer_name=customers[0],
            start_date=group["period_months"][0],
            end_date=group["period_months"][1],
            responsibilities=responsibilities,
            source_refs=refs,
        )
        projects.append(project)
        private_sources.append({
            "candidate_index": idx,
            "group_id": group_id,
            "source_status": group["status"],
            "native_pdf_row_sources": sources,
            "ai_suggestion_indices": hits,
            "root_quote_refs_reverified": len(refs),
            "requires_name_merge_before_registration": False,
        })

    proposed = original.model_copy(update={"projects": projects})
    old_content = original.model_dump(mode="json")
    new_content = proposed.model_dump(mode="json")
    if any(old_content[k] != new_content[k] for k in old_content if k != "projects"):
        raise ValueError("NATIVE_V30_NONPROJECT_CORE_CHANGED")

    # Realistic quality-guard dry-run, with original page text available.
    guard = apply_candidate_quality_guards(
        proposed, confirmed_person_name=original.profile.name,
        page_texts=allowed_pages,
        document_type_by_id=None, strict_section_authority=False,
    )
    guarded = apply_candidate_quality_guards(
        proposed, confirmed_person_name=original.profile.name,
        page_texts=allowed_pages,
        document_type_by_id=None, strict_section_authority=True,
    )
    identities = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in projects
    ]
    safe_ident = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in guard.projects
    ]
    strict_ident = [
        (p.project_name, p.customer_name, p.start_date, p.end_date)
        for p in guarded.projects
    ]
    if identities != safe_ident:
        raise ValueError("NATIVE_V30_DEFAULT_GUARD_DROPPED_SOURCE_ROW")
    # Strict guard may reject rows. Report the count; do not mutate source
    # candidates nor silently hide project records.

    protected = build_source_first_review_safe_diffs(
        proposed, latest_confirmed_snapshot
    )
    specs = protected.specs
    mapped_indices = {
        int(match.group(1))
        for spec in specs
        if spec.entity_type == "PROJECT"
        if (match := PROJECT_PATH.match(spec.candidate_path or ""))
    }
    if mapped_indices != set(range(len(projects))):
        raise ValueError("NATIVE_V30_EXISTING_DIFF_LOST_SOURCE_PROJECT")
    project_specs = [x for x in specs if x.entity_type == "PROJECT"]
    hint_pairs = clusters.get("review_suggestions", [])
    return NativeDirectPreview(
        candidate=proposed,
        specs=specs,
        diagnostics={
            "mode": "v30_native_pdf_direct_to_candidate_diff_readonly",
            "native_pdf_input_rows": len(native_rows),
            "native_project_groups": len(groups),
            "source_status_counts": clusters.get("status_counts", {}),
            "candidate_project_count": len(projects),
            "represented_project_candidates": len(mapped_indices),
            "source_roles_preserved_count": source_role_count,
            "native_group_root_quotes_reverified_count": evidence_bound_count,
            "literal_quote_ref_count": preserved_quote_ref_count,
            "unique_identity_ai_suggestion_group_count": sum(len(h) == 1 for h in group_ai),
            "unmatched_native_group_count": sum(not h for h in group_ai),
            "unmatched_original_ai_project_candidates": (
                len(original.projects) - len(ai_matches)
            ),
            "name_variant_review_suggestions_kept_separate": len(hint_pairs),
            "default_guard_preserves_all_native_projects": identities == safe_ident,
            "strict_guard_preserves_all_native_projects": identities == strict_ident,
            "strict_guard_project_count": len(guarded.projects),
            "project_diff_type_counts": dict(Counter(x.change_type for x in project_specs)),
            "title_variant_auto_links_blocked": len(
                protected.variant_review_indices
            ),
            "duplicate_target_auto_links_blocked": len(
                protected.collision_review_indices
            ),
            "v30_source_first_safe_diff_enabled": True,
            "total_diff_count": len(specs),
            "nonproject_candidate_unchanged": True,
            "no_model_calls_or_operational_writes": True,
            "not_a_runnable_or_confirmable_analysis": True,
        },
        private_source_provenance=private_sources,
    )
