#!/usr/bin/env python3
"""Read-only PROJECTS prompt evaluation using an existing AnalysisRun's pages.

Default mode is preflight (no AI calls). --live runs PROJECTS-only extraction
for profile-extract-v25 vs profile-extract-v26 against identical stored text
and code catalog in memory. CORE is unchanged between those variants and is
deliberately not re-analyzed.

`--solo-version profile-extract-v27|v28|v29` evaluates that opt-in version
alone (no v25/v26 comparison). No AnalysisRun creation, candidate/diff
persistence, VLM or profile mutation. Results are diagnostics, not an
accuracy verdict without a reviewed ground truth.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text

from app.ai.prompts.profile_extract import resolve_profile_prompt
from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.service import AnalysisService
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
)
from app.storage.s3 import get_object_storage
from scripts.profile_project_reference import _fold, _month, evaluate_projects, load_reference

COMPARE_VERSIONS = ("profile-extract-v25", "profile-extract-v26")
SOLO_VERSIONS = (
    "profile-extract-v27",
    "profile-extract-v28",
    "profile-extract-v29",
)
VERSIONS = COMPARE_VERSIONS
ROLE_ONLY = frozenset({"PM", "PL", "PMO", "TA", "SA", "AA", "개발", "운영", "사업관리"})


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def project_metrics(candidate) -> dict:
    """Only aggregate data; source quotes and names remain in memory."""
    projects = list(candidate.projects)
    keys = [
        (
            _normalized(project.project_name),
            _normalized(project.customer_name),
            _normalized(project.start_date),
            _normalized(project.end_date),
        )
        for project in projects
    ]
    duplicates = sum(n - 1 for n in Counter(keys).values() if n > 1)
    roles = sorted({
        str(p.project_name).strip().upper()
        for p in projects
        if (p.project_name or "").strip().upper() in ROLE_ONLY
    })
    return {
        "project_count": len(projects),
        "role_only_project_count": sum(
            (p.project_name or "").strip().upper() in ROLE_ONLY for p in projects
        ),
        "role_only_labels": roles,
        "unnamed_project_count": sum(not (p.project_name or "").strip() for p in projects),
        "duplicate_identity_count": duplicates,
        "project_evidence_count": sum(bool(p.source_refs) for p in projects),
    }


def compare_projects(baseline, experiment, *, include_names: bool = False) -> dict:
    def key(project) -> tuple[str, str, str, str]:
        return (
            _normalized(project.project_name),
            _normalized(project.customer_name),
            _normalized(project.start_date),
            _normalized(project.end_date),
        )

    a = Counter(key(project) for project in baseline.projects)
    b = Counter(key(project) for project in experiment.projects)
    only_a = list((a - b).elements())
    only_b = list((b - a).elements())
    summary = {
        "v25_only_count": len(only_a),
        "v26_only_count": len(only_b),
        "common_identity_count": sum((a & b).values()),
    }
    if include_names:
        # Caller explicitly opts in; content stays on local stdout only.
        summary["v25_only_identities"] = only_a
        summary["v26_only_identities"] = only_b
    return summary


def diagnose_projects(candidate, reference: dict, *, page_texts, allowed_documents) -> dict:
    """Explain reference mismatches/duplicate titles without persisting candidates.

    This opt-in report contains title/customer/date values and source locations,
    but never full page text, source quotes, or personal profile data. Nearby
    periods are clues, not an ownership verdict or a suggested automatic fix.
    """
    from app.modules.analysis import compact_v8

    projects = list(candidate.projects)
    rows = reference["projects"]
    title_to_index = {
        _fold(title): i
        for i, row in enumerate(rows)
        for title in [row["project_name"], *row.get("aliases", [])]
    }
    groups: dict[int, list[tuple[int, object]]] = {i: [] for i in range(len(rows))}
    unexpected: list[tuple[int, object]] = []
    for i, project in enumerate(projects):
        index = title_to_index.get(_fold(project.project_name))
        if index is None:
            unexpected.append((i, project))
        else:
            groups[index].append((i, project))

    def matches(project, row) -> dict[str, bool]:
        customers = [row["customer_name"], *row.get("customer_aliases", [])]
        return {
            "customer": _fold(project.customer_name) in {_fold(c) for c in customers},
            "start_month": _month(project.start_date) == _month(row["start_date"]),
            "end_month": _month(project.end_date) == _month(row["end_date"]),
        }

    def evidence(project) -> list[dict]:
        refs = []
        for ref in project.source_refs or []:
            doc_id = (ref.document_id or "").strip()
            page_no = ref.page_no
            quote = (ref.quote_text or "").strip()
            page = page_texts.get((doc_id, page_no), "")
            pos = page.find(quote) if quote else -1
            allowed = allowed_documents.get(doc_id)
            period_clues = []
            if pos >= 0:
                ranges = compact_v8._project_full_range_re(
                    flexible_period_parse=True
                ).finditer(page)
                for match in ranges:
                    parsed = compact_v8.parse_project_date_range_from_texts(
                        [match.group(0)],
                        project_name=project.project_name,
                        flexible_period_parse=True,
                    )
                    if parsed is None:
                        continue
                    distance = max(
                        pos - match.end(),
                        match.start() - (pos + len(quote)),
                        0,
                    )
                    period_clues.append((distance, match.start(), parsed))
                period_clues.sort(key=lambda item: (item[0], item[1]))
            refs.append({
                "document_id": doc_id,
                "page_no": page_no,
                "quote_sha256_16": hashlib.sha256(
                    quote.encode("utf-8")
                ).hexdigest()[:16] if quote else None,
                "quote_exact_on_page": pos >= 0,
                "page_allowed": allowed is not None and (
                    not allowed or page_no in allowed
                ),
                "name_seen_on_page": compact_v8._line_has_ws_folded_phrase(
                    page, project.project_name or ""
                ) if project.project_name else False,
                "customer_seen_on_page": compact_v8._line_has_ws_folded_phrase(
                    page, project.customer_name or ""
                ) if project.customer_name else False,
                "nearby_page_periods_unverified": [
                    {"start_date": p[0], "end_date": p[1], "distance_chars": distance}
                    for distance, _, p in period_clues[:3]
                ],
            })
        return refs

    def describe(index: int, project, row: dict | None) -> dict:
        matches_by_field = matches(project, row) if row is not None else None
        source_pages = {
            (ref.document_id, ref.page_no) for ref in project.source_refs or []
        }
        same_title_customer_page = sum(
            compact_v8._fold_ws_text(other.project_name or "")
            == compact_v8._fold_ws_text(project.project_name or "")
            and compact_v8._fold_ws_text(other.customer_name or "")
            == compact_v8._fold_ws_text(project.customer_name or "")
            and any(
                (ref.document_id, ref.page_no) in source_pages
                for ref in other.source_refs or []
            )
            for other in projects if other is not project
        )
        owned = compact_v8._unique_owned_root_project_period(
            candidate,
            project,
            page_texts=page_texts,
            allowed_documents=allowed_documents,
            flexible_period_parse=True,
        )
        source_evidence = evidence(project)
        if owned is not None:
            owner_status = "verified_unique_owned_period"
        elif not (project.project_name and project.customer_name and project.source_refs):
            owner_status = "missing_identity_or_root_refs"
        elif same_title_customer_page:
            owner_status = "shared_title_customer_reference_page"
        elif any(not ref["quote_exact_on_page"] for ref in source_evidence):
            owner_status = "root_quote_not_exact_on_page"
        else:
            owner_status = "unresolved_ownership_or_period_block"
        return {
            "candidate_index": index,
            "project_name": project.project_name,
            "customer_name": project.customer_name,
            "start_date": project.start_date,
            "end_date": project.end_date,
            "field_matches": matches_by_field,
            "shared_title_customer_ref_page_other_count": same_title_customer_page,
            "owner_probe_status": owner_status,
            "unique_owned_period": list(owned) if owned is not None else None,
            "source_refs": source_evidence,
        }

    problematic: list[dict] = []
    duplicate_extras = 0
    field_issue_groups = 0
    missing = []
    for row_index, row in enumerate(rows):
        found = groups[row_index]
        if not found:
            missing.append({
                "reference_id": row.get("id", row_index + 1),
                "project_name": row["project_name"],
            })
            continue
        duplicate_extras += len(found) - 1
        best_index, best = max(
            found,
            key=lambda item: sum(matches(item[1], row).values()),
        )
        best_ok = all(matches(best, row).values())
        if not best_ok:
            field_issue_groups += 1
        if len(found) == 1 and best_ok:
            continue
        problematic.append({
            "reference_id": row.get("id", row_index + 1),
            "project_name": row["project_name"],
            "expected": {
                "customer_name": row["customer_name"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
            },
            "best_candidate_index_for_scoring": best_index,
            "candidate_count": len(found),
            "candidates": [describe(i, p, row) for i, p in found],
        })

    return {
        "scope": "in_memory_reference_mismatches_and_duplicate_titles_only",
        "privacy": "no_page_text_or_source_quotes_in_output",
        "nearby_periods_policy": "unverified_proximity_clues_not_ownership_or_fixes",
        "duplicate_title_extra_count": duplicate_extras,
        "field_issue_group_count": field_issue_groups,
        "diagnostic_group_count": len(problematic),
        "missing_reference_titles": missing,
        "unexpected_candidates": [describe(i, p, None) for i, p in unexpected],
        "groups": problematic,
    }


@dataclass(frozen=True)
class Snapshot:
    run_status: str
    candidate_sha: str
    documents_sha: str
    documents: tuple[DocumentSnapshot, ...]
    codes: list
    aliases: dict
    base_profile_name: str | None


def _read_snapshot(run_id: UUID) -> Snapshot:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        repo = AnalysisRepository(db)
        run = repo.get_run(run_id)
        assert run is not None, "ANALYSIS_RUN_NOT_FOUND"
        ids = repo.list_run_document_ids(run_id)
        rows = repo.list_documents_by_ids(ids)
        assert ids and len(rows) == len(ids), "RUN_DOCUMENTS_MISSING"
        docs: list[DocumentSnapshot] = []
        for doc, group, type_name in rows:
            assert group.person_id == run.person_id, "DOCUMENT_PERSON_MISMATCH"
            pages = repo.list_document_pages(doc.id)
            docs.append(DocumentSnapshot(
                id=doc.id,
                original_filename=doc.original_filename,
                extension=doc.extension,
                mime_type=doc.mime_type,
                storage_key=doc.storage_key,
                preview_storage_key=doc.preview_storage_key,
                document_type_code=group.document_type_code,
                document_type_name=type_name,
                version_no=doc.version_no,
                person_id=group.person_id,
                pages=[PageSnapshot(
                    page_no=page.page_no,
                    extracted_text=page.extracted_text,
                    layout_json=copy.deepcopy(page.layout_json),
                    extraction_method=page.extraction_method,
                ) for page in pages],
            ))
        codes = repo.list_active_codes()
        aliases = repo.list_aliases_for_codes([code.code for code in codes])
        base = (
            repo.get_profile_revision_snapshot(run.person_id, run.base_profile_version)
            if run.base_profile_version is not None else None
        )
        profile = base.get("profile") if isinstance(base, dict) else None
        name = profile.get("name") if isinstance(profile, dict) else None
        signature = [
            (str(d.id), [(p.page_no, p.extracted_text, p.extraction_method, p.layout_json)
                         for p in d.pages])
            for d in docs
        ]
        return Snapshot(
            run_status=run.status,
            candidate_sha=_fingerprint(run.candidate_json),
            documents_sha=_fingerprint(signature),
            documents=tuple(docs),
            codes=codes,
            aliases=aliases,
            base_profile_name=name,
        )


def _verify_unchanged(run_id: UUID, original: Snapshot) -> None:
    current = _read_snapshot(run_id)
    assert current.run_status == original.run_status, "ANALYSIS_STATUS_CHANGED"
    assert current.candidate_sha == original.candidate_sha, "CANDIDATE_CHANGED"
    assert current.documents_sha == original.documents_sha, "DOCUMENT_PAGES_CHANGED"


class MeteredLLM(OpenAICompatibleLLMProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.json_calls = 0

    def complete_json(self, **kwargs):
        self.json_calls += 1
        return super().complete_json(**kwargs)


def _run_version(*, version, snapshot, prompt_source, settings, storage, run_id) -> tuple:
    prompt = resolve_profile_prompt(version)
    llm = MeteredLLM(settings)
    service = AnalysisService(None, storage=storage, settings=settings, llm=llm)
    # The extraction method only uses these read-only catalog operations.
    service.repo = SimpleNamespace(
        list_active_codes=lambda: snapshot.codes,
        list_aliases_for_codes=lambda _codes: snapshot.aliases,
    )
    catalog_map = {
        code.code: (code.code_type, bool(code.is_active))
        for code in snapshot.codes
    }
    blocks = prompt_source.text
    started = time.monotonic()
    candidate = service._extract_staged_compact_candidate(
        prompt=prompt,
        document_blocks=blocks,
        catalog_map=catalog_map,
        allowed_documents=prompt_source.allowed_documents,
        page_texts=prompt_source.page_texts,
        documents=snapshot.documents,
        source_char_count=len(blocks.strip()),
        base_log={
            "analysis_run_id": str(run_id),
            "pilot": "profile_prompt_ab_read_only",
            "document_count": len(snapshot.documents),
            "vlm_pages": 0,
            "prompt_version": version,
            "source_char_count": len(blocks.strip()),
        },
        vlm_pages=0,
        projects_only=True,
    )
    types = {
        str(doc.id): str(doc.document_type_code)
        for doc in snapshot.documents if doc.document_type_code
    }
    guarded = apply_candidate_quality_guards(
        candidate,
        confirmed_person_name=snapshot.base_profile_name,
        page_texts=prompt_source.page_texts,
        document_type_by_id=types,
        strict_section_authority=prompt.guard_core_section_authority,
    )
    return guarded, {
        **project_metrics(guarded),
        "llm_calls": llm.json_calls,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--live", action="store_true", help="LLM analyses; no DB writes")
    parser.add_argument("--include-project-names", action="store_true")
    parser.add_argument(
        "--diagnose-projects",
        action="store_true",
        help=(
            "Opt-in v29 evidence diagnostics for reference mismatches and "
            "duplicate titles. Requires --live, --solo-version v29, "
            "--ground-truth-file and --include-project-names."
        ),
    )
    parser.add_argument(
        "--solo-version",
        choices=SOLO_VERSIONS,
        help=(
            "Evaluate one opt-in version alone. "
            "Default without this flag remains v25 vs v26 comparison."
        ),
    )
    parser.add_argument(
        "--ground-truth-file",
        type=Path,
        help="Optional project reference JSON path or '-' for stdin",
    )
    args = parser.parse_args()
    if args.diagnose_projects and not (
        args.live
        and args.solo_version == "profile-extract-v29"
        and args.ground_truth_file is not None
        and args.include_project_names
    ):
        parser.error(
            "--diagnose-projects requires --live --solo-version "
            "profile-extract-v29 --ground-truth-file and --include-project-names"
        )
    reference = load_reference(args.ground_truth_file) if args.ground_truth_file else None
    versions = (args.solo_version,) if args.solo_version else COMPARE_VERSIONS
    compare_mode = args.solo_version is None

    settings = get_settings()
    snapshot = _read_snapshot(args.run_id)
    # No VLM: reuse only the stored, already-extracted page texts for both arms.
    no_vlm_settings = settings.model_copy(update={
        "analysis_max_vlm_pages": 0,
        "analysis_vlm_table_banding_reprocess_done": False,
    })
    storage = get_object_storage()
    builder = AnalysisSourceBuilder(storage, settings=no_vlm_settings, vlm=None)
    bundle = builder.build(
        list(snapshot.documents),
        log_context={"pilot": "profile_prompt_ab"},
    )
    assert not bundle.vlm_transcriptions and bundle.total_vlm_pages == 0
    source = bundle.build_prompt_source(int(settings.analysis_max_total_text_chars))
    assert source.text.strip(), "EMPTY_STORED_DOCUMENT_SOURCE"
    page_count = sum(len(d.pages) for d in snapshot.documents)
    report = {
        "run_id": str(args.run_id),
        "mode": "live" if args.live else "preflight",
        "source_mode": "stored_document_pages_only_no_vlm",
        "evaluation_scope": "projects_only_shared_core_skipped",
        "evaluation_mode": "compare_v25_v26" if compare_mode else f"solo_{versions[0]}",
        "versions": list(versions),
        "run_status": snapshot.run_status,
        "document_count": len(snapshot.documents),
        "stored_pages": page_count,
        "pages_in_prompt": len(source.page_texts),
        "prompt_source_chars": len(source.text),
        "existing_candidate_sha256": snapshot.candidate_sha,
    }
    if reference is not None:
        report["reference"] = {
            "review_status": reference["review_status"],
            "project_count": len(reference["projects"]),
            "matching": "reviewed title aliases + customer + year/month dates",
        }
    try:
        if args.live:
            results = {}
            candidates = {}
            for version in versions:
                candidate, stats = _run_version(
                    version=version,
                    snapshot=snapshot,
                    prompt_source=source,
                    settings=settings,
                    storage=storage,
                    run_id=args.run_id,
                )
                candidates[version] = candidate
                results[version] = stats
            report["results"] = results
            if compare_mode:
                report["comparison"] = compare_projects(
                    candidates[versions[0]], candidates[versions[1]],
                    include_names=args.include_project_names,
                )
            if reference is not None:
                report["reference_evaluation"] = {
                    version: evaluate_projects(
                        candidates[version], reference,
                        include_names=args.include_project_names,
                    )
                    for version in versions
                }
            if args.diagnose_projects:
                report["project_diagnostics"] = diagnose_projects(
                    candidates["profile-extract-v29"],
                    reference,
                    page_texts=source.page_texts,
                    allowed_documents=source.allowed_documents,
                )
            report["interpretation"] = (
                "Provisional reference: verify against original document layout"
                if reference is not None and reference["review_status"] == "provisional"
                else "Manual ground-truth review required"
            )
    finally:
        _verify_unchanged(args.run_id, snapshot)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("TALENTSCOPE_PROMPT_AB_READONLY_PASS")


if __name__ == "__main__":
    main()
