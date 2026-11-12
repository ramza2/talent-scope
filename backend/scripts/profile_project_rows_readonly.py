#!/usr/bin/env python3
"""Read-only PDF project-row grouping probe for an existing TalentScope AnalysisRun.

Default: PyMuPDF native tables only, no AI calls. --allow-vlm explicitly opts
into the existing scanned-table band transcriber, with a hard call cap.
Results stay in memory and stdout, never in the database or object storage.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.ai.providers.vlm import OpenAICompatibleVLMProvider
from app.modules.analysis.project_row_grouping import native_table_groups, vlm_band_groups
from app.modules.analysis.source_builder import AnalysisSourceBuilder
from app.storage.s3 import get_object_storage
from scripts.profile_prompt_ab_readonly import _read_snapshot, _verify_unchanged


def run(args: argparse.Namespace) -> dict:
    snapshot = _read_snapshot(args.run_id)
    if snapshot.run_status != "CONFIRMED":
        raise ValueError("REQUIRES_CONFIRMED_RUN")
    if args.expected_candidate_sha and snapshot.candidate_sha != args.expected_candidate_sha:
        raise ValueError("CANDIDATE_SHA_MISMATCH")

    vlm = OpenAICompatibleVLMProvider() if args.allow_vlm else None
    builder = AnalysisSourceBuilder(get_object_storage(), vlm=vlm)
    all_rows: list[dict] = []
    doc_reports: list[dict] = []
    remaining = args.max_vlm_calls
    try:
        for doc in snapshot.documents:
            stats = {"document_id": str(doc.id), "pages": len(doc.pages),
                     "rendered_pages": 0, "native_groups": 0,
                     "vlm_groups": 0, "vlm_calls": 0, "unresolved_pages": 0}
            pdf = builder._open_render_source(doc)
            try:
                for page in doc.pages:
                    if pdf is None or not 1 <= page.page_no <= len(pdf):
                        stats["unresolved_pages"] += 1
                        continue
                    stats["rendered_pages"] += 1
                    pdf_page = pdf.load_page(page.page_no - 1)
                    native = native_table_groups(pdf_page, str(doc.id), page.page_no)
                    if native:
                        all_rows.extend(native)
                        stats["native_groups"] += len(native)
                        continue
                    if args.allow_vlm and remaining >= 2:
                        band_text, calls = builder._try_transcribe_table_bands(
                            pdf_page,
                            log_context={"pilot": "project_row_groups_readonly",
                                         "document_id": str(doc.id), "page_no": page.page_no},
                            remaining_calls=remaining,
                        )
                        stats["vlm_calls"] += calls
                        remaining -= calls
                        if band_text:
                            lines = band_text.splitlines()
                            # _try_transcribe_table_bands has already verified
                            # each band has exactly one line per detected row.
                            grouped = vlm_band_groups(
                                band_text, doc_id=str(doc.id), page_no=page.page_no,
                                first_row=1, last_row=len(lines),
                            )
                            all_rows.extend(grouped)
                            stats["vlm_groups"] += len(grouped)
                            if grouped:
                                continue
                    stats["unresolved_pages"] += 1
            finally:
                if pdf is not None:
                    pdf.close()
            doc_reports.append(stats)
    finally:
        _verify_unchanged(args.run_id, snapshot)

    results = {
        "mode": "readonly_in_memory_project_row_groups",
        "run_id": str(args.run_id),
        "source_mode": "original_or_preview_pdf_native_table_then_opt_in_vlm_band",
        "stored_page_count": sum(len(d.pages) for d in snapshot.documents),
        "candidate_sha256_unchanged": snapshot.candidate_sha,
        "vlm_opt_in": args.allow_vlm,
        "vlm_call_count": sum(r["vlm_calls"] for r in doc_reports),
        "group_count": len(all_rows),
        "mapped_group_count": sum(bool(r["column_mapping_verified"]) for r in all_rows),
        "groups_by_source": dict(Counter(r["source"] for r in all_rows)),
        "documents": doc_reports,
        "interpretation": "Row grouping only; no LLM extraction, identity merge, or date corrections",
    }
    if args.include_rows:
        results["groups"] = all_rows
    else:
        results["group_locations"] = [
            {"row_id": r["row_id"], "document_id": r["document_id"],
             "page_no": r["page_no"], "source": r["source"],
             "bbox": r["bbox"], "mapped": r["column_mapping_verified"]}
            for r in all_rows
        ]
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--expected-candidate-sha", default="")
    parser.add_argument("--allow-vlm", action="store_true")
    parser.add_argument("--max-vlm-calls", type=int, default=0)
    parser.add_argument("--include-rows", action="store_true",
                        help="Print project table cells; keep output private")
    args = parser.parse_args()
    if args.max_vlm_calls < 0 or (args.allow_vlm and args.max_vlm_calls < 2) or (
        not args.allow_vlm and args.max_vlm_calls != 0
    ):
        parser.error("--allow-vlm requires --max-vlm-calls >= 2; otherwise limit must be 0")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("TALENTSCOPE_PROJECT_ROWS_READONLY_PASS")


if __name__ == "__main__":
    main()
