"""Read-only bridge from v30 Source-First preview to existing Candidate schema.

NO operational import/use. This is an explicitly opted-in pilot to check
whether native PDF project identities survive candidate validation and Diff.
It never merges different source groups or silently adopts LLM enrichments.

Where a UNIQUE source-matching AI candidate has already been independently
verified as having literal original-page ROOT evidence, preserve those exact
source_refs; otherwise leave refs empty instead of fabricating PDF quotations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument, ProjectCandidate, SourceRef,
)
from app.modules.analysis.project_source_first_candidate_v30 import _fold, _ym

PREVIEW_KIND = "talentscope_private_source_first_candidate_preview_v30"
AI_KIND = "talentscope_readonly_project_candidate_fixture"


@dataclass
class BridgeResult:
    candidate: ProfileCandidateDocument
    diagnostics: dict[str, Any]


def make_candidate_bridge(
    private_preview: dict[str, Any],
    private_ai_fixture: dict[str, Any],
) -> BridgeResult:
    if (
        private_preview.get("kind") != PREVIEW_KIND
        or private_preview.get("mode") != "read_only_review_preview_not_operational_candidate"
        or private_ai_fixture.get("kind") != AI_KIND
        or not private_preview.get("run_id")
        or private_preview["run_id"] != private_ai_fixture.get("run_id")
        or private_preview.get("source_sha256") != private_ai_fixture.get("source_sha256")
        or private_preview.get("prior_candidate_sha256") !=
            private_ai_fixture.get("existing_candidate_sha256")
    ):
        raise ValueError("BRIDGE_FIXTURE_FINGERPRINT_MISMATCH")
    rows = private_preview.get("projects")
    ai_rows = private_ai_fixture.get("projects")
    if (
        not isinstance(rows, list) or not rows
        or not isinstance(ai_rows, list)
        or not all(isinstance(r, dict) for r in ai_rows)
        or private_preview.get("project_count") != len(rows)
    ):
        raise ValueError("BRIDGE_SOURCE_PREVIEW_INCOMPLETE")

    seen_groups: set[str] = set()
    used_candidate_indices: set[int] = set()
    projects: list[ProjectCandidate] = []
    ref_group_indices: list[int] = []
    no_ref_group_indices: list[int] = []
    source_only_group_indices: list[int] = []
    multiple_candidates_group_indices: list[int] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"BRIDGE_GROUP_INVALID:{index}")
        gid = row.get("group_id")
        source = row.get("source_project")
        locations = row.get("native_row_provenance")
        suggestions = row.get("enrichment_suggestions")
        preferred = row.get("preferred_enrichment_candidate_index")
        if (
            not isinstance(gid, str) or not gid or gid in seen_groups
            or not isinstance(source, dict)
            or not isinstance(locations, list) or not locations
            or not isinstance(suggestions, list)
            or row.get("source_status") not in {
                "verified_exact_multi_document", "single_source_unmatched"
            }
            or row.get("requires_pre_registration_name_merge") is not False
            or row.get("automatic_confirmation") is not False
            or row.get("suitable_for_user_bulk_review_preview") is not True
            or row.get("source_refs_not_fabricated") is not True
        ):
            raise ValueError(f"BRIDGE_GROUP_CONTRACT_INVALID:{index}")
        seen_groups.add(gid)
        title, customer = source.get("project_name"), source.get("customer_name")
        title_aliases = source.get("original_titles")
        customer_aliases = source.get("original_customers")
        start, end = source.get("start_date"), source.get("end_date")
        if (
            not isinstance(title, str) or not title.strip()
            or not isinstance(customer, str) or not customer.strip()
            or not isinstance(title_aliases, list) or title not in title_aliases
            or not isinstance(customer_aliases, list) or customer not in customer_aliases
            or not isinstance(start, str) or _ym(start) != start
            or not isinstance(end, str) or
                _ym(end, allow_ongoing=True) != end
        ):
            raise ValueError(f"BRIDGE_SOURCE_IDENTITY_INVALID:{index}")
        source_slots = set()
        for loc in locations:
            if (
                not isinstance(loc, dict)
                or not isinstance(loc.get("document_id"), str) or not loc["document_id"]
                or type(loc.get("page_no")) is not int or loc["page_no"] <= 0
                or not isinstance(loc.get("row_id"), str) or not loc["row_id"]
            ):
                raise ValueError(f"BRIDGE_SOURCE_PROVENANCE_INVALID:{index}")
            source_slots.add((loc["document_id"], loc["page_no"]))

        indices: list[int] = []
        verified: list[int] = []
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                raise ValueError(f"BRIDGE_SUGGESTION_FORMAT_INVALID:{index}")
            ai_index = suggestion.get("candidate_index")
            if (
                type(ai_index) is not int or ai_index < 0 or ai_index >= len(ai_rows)
                or ai_index in used_candidate_indices
            ):
                raise ValueError(f"BRIDGE_SUGGESTION_INDEX_INVALID:{index}")
            used_candidate_indices.add(ai_index)
            indices.append(ai_index)
            item = ai_rows[ai_index]
            title_matches = _fold(item.get("project_name")) in {
                _fold(name) for name in title_aliases
            }
            customer_matches = _fold(item.get("customer_name")) in {
                _fold(name) for name in customer_aliases
            }
            time_matches = (
                _ym(item.get("start_date")) == start
                and _ym(item.get("end_date"), allow_ongoing=True) == end
            )
            claimed_identity = suggestion.get("candidate_title_customer_match")
            claimed_time = suggestion.get("candidate_period_matches_original_months")
            claimed_root = suggestion.get("candidate_root_evidence_verified")
            if claimed_identity is not (title_matches and customer_matches):
                raise ValueError(f"BRIDGE_SUGGESTION_IDENTITY_MISMATCH:{index}")
            if claimed_time is not time_matches:
                raise ValueError(f"BRIDGE_SUGGESTION_PERIOD_MISMATCH:{index}")
            if claimed_root:
                if not (title_matches and customer_matches and time_matches):
                    raise ValueError(f"BRIDGE_UNVERIFIED_ROOT:{index}")
                verified.append(ai_index)

        if indices and len(indices) > 1:
            multiple_candidates_group_indices.append(index)
        if not indices:
            source_only_group_indices.append(index)

        # The source-first pilot selected one ROOT evidence candidate only when
        # it was uniquely matched. Never use arbitrary first/last candidates.
        if preferred is not None and (
            type(preferred) is not int
            or verified != [preferred]
        ):
            raise ValueError(f"BRIDGE_PREFERRED_NOT_UNIQUE:{index}")
        if preferred is None and len(verified) == 1:
            raise ValueError(f"BRIDGE_VERIFIED_REF_OMITTED:{index}")

        root_refs = []
        if preferred is not None:
            raw_refs = ai_rows[preferred].get("source_refs")
            if not isinstance(raw_refs, list) or not raw_refs:
                raise ValueError(f"BRIDGE_VERIFIED_REF_MISSING:{index}")
            for raw_ref in raw_refs:
                if not isinstance(raw_ref, dict):
                    raise ValueError(f"BRIDGE_VERIFIED_REF_INVALID:{index}")
                doc_id, page = raw_ref.get("document_id"), raw_ref.get("page_no")
                quote = raw_ref.get("quote_text")
                if (
                    (str(doc_id), page) not in source_slots
                    or not isinstance(quote, str) or not quote.strip()
                    or len(quote) > 500
                ):
                    raise ValueError(f"BRIDGE_VERIFIED_REF_NOT_FROM_SOURCE:{index}")
                root_refs.append(SourceRef(
                    document_id=str(doc_id), page_no=page, quote_text=quote
                ))
            ref_group_indices.append(index)
        else:
            no_ref_group_indices.append(index)

        # All code/role suggestions are preserved in the separate private
        # preview; NEVER copy them into a live-like Pydantic candidate until
        # each relation's evidence is independently validated.
        projects.append(ProjectCandidate(
            project_name=title, customer_name=customer,
            start_date=start, end_date=end, source_refs=root_refs,
        ))

    unlinked = private_preview.get("unlinked_ai_candidate_indices")
    if (
        not isinstance(unlinked, list)
        or any(type(i) is not int or i < 0 or i >= len(ai_rows) for i in unlinked)
        or len(set(unlinked)) != len(unlinked)
        or used_candidate_indices & set(unlinked)
        or used_candidate_indices | set(unlinked) != set(range(len(ai_rows)))
    ):
        raise ValueError("BRIDGE_AI_COVERAGE_INVALID")
    pairs = private_preview.get("name_variant_pairs_remain_independent")
    if (
        not isinstance(pairs, list)
        or any(not isinstance(pair, list) or len(pair) != 2
               or pair[0] == pair[1] or any(g not in seen_groups for g in pair)
               for pair in pairs)
    ):
        raise ValueError("BRIDGE_NAME_VARIANT_PAIR_INVALID")

    candidate = ProfileCandidateDocument(projects=projects)
    return BridgeResult(
        candidate=candidate,
        diagnostics={
            "run_id": private_preview["run_id"],
            "source_project_count": len(rows),
            "candidate_project_count": len(projects),
            "root_original_quote_ref_groups": len(ref_group_indices),
            "root_unquoted_groups": len(no_ref_group_indices),
            "source_only_groups": len(source_only_group_indices),
            "multiple_ai_suggestions_groups": len(multiple_candidates_group_indices),
            "ai_suggestion_count": len(used_candidate_indices),
            "unlinked_ai_count": len(unlinked),
            "name_variant_pair_count": len(pairs),
            "all_name_variant_pairs_preserved_separately": True,
            "root_ref_indices": ref_group_indices,
            "root_no_ref_indices": no_ref_group_indices,
            "no_ai_enrichment_auto_promoted": True,
            "no_fabricated_source_quotes": True,
        },
    )
