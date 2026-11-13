"""Read-only, no-model-call verification of opt-in v30 PPTX source rows.

Prints counts only (never source text, names, customers, or project keys).
Runs in a temporary API container with backend code mounted read-only.
"""
from __future__ import annotations

from collections import Counter
import re
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select, text

from app.db.models.analysis import AnalysisRun, AnalysisRunDocument
from app.db.models.document import Document, DocumentPage
from app.db.models.project import Project
from app.db.session import SessionLocal
from app.modules.analysis.pptx_native_source_v30 import pptx_native_table_rows
from app.modules.analysis.project_row_match_v30 import build_clusters, folded
from app.modules.analysis.project_source_first_native_direct_v30 import (
    assemble_native_direct,
)
from app.storage.s3 import get_object_storage

RUN = UUID("19fdf976-5764-4f1e-b5ca-85d5cdb7dd61")


def _month(value):
    if value is None:
        return None
    if hasattr(value, "strftime"):
        return value.strftime("%Y.%m")
    value = str(value).strip()
    if value.casefold() in {"현재", "진행중", "재직중", "present"}:
        return "현재"
    match = re.fullmatch(
        r"((?:19|20)\d{2})[.\-/](0?[1-9]|1[0-2])"
        r"(?:[.\-/](?:0?[1-9]|[12]\d|3[01]))?",
        value,
    )
    if match is None:
        return None
    return f"{match[1]}.{int(match[2]):02d}"


def _key(name, customer, start, end):
    name_key = folded(name)
    customer_key = folded(customer)
    start_key = _month(start)
    end_key = _month(end)
    if not (name_key and customer_key and start_key and end_key):
        return None
    return name_key, customer_key, start_key, end_key


def main():
    rows = []
    builder = SimpleNamespace(storage=get_object_storage())
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
        run = db.get(AnalysisRun, RUN)
        assert run is not None and run.status == "CONFIRMED"
        docs = db.scalars(
            select(Document)
            .join(AnalysisRunDocument, AnalysisRunDocument.document_id == Document.id)
            .where(AnalysisRunDocument.analysis_run_id == RUN)
            .order_by(Document.id)
        ).all()
        assert len(docs) == 2
        for i, doc in enumerate(docs, 1):
            assert doc.extension and doc.extension.lower() == "pptx"
            assert doc.processing_status == "READY" and doc.deleted_at is None
            pages = db.scalars(
                select(DocumentPage.page_no)
                .where(DocumentPage.document_id == doc.id)
                .order_by(DocumentPage.page_no)
            ).all()
            snapshot = SimpleNamespace(
                id=doc.id,
                extension=doc.extension,
                storage_key=doc.storage_key,
                pages=[SimpleNamespace(page_no=p) for p in pages],
            )
            try:
                extracted = pptx_native_table_rows(
                    source_builder=builder, document=snapshot,
                )
            except ValueError as exc:
                print(f"DOC_{i}: FALLBACK {str(exc).split(':')[0]}")
                print("RESULT: FALLBACK_REQUIRED")
                return
            rows.extend(extracted)
            print(f"DOC_{i}: PASS ROWS={len(extracted)}")
            print(f"DOC_{i}_ZERO_HEIGHT_PROJECTS:",
                  sum(row["bbox"] is None for row in extracted))

        clusters = build_clusters(rows)
        counts = Counter(g["status"] for g in clusters["groups"])
        source_key_values = [
            _key(g["project_names"][0], g["customers"][0], *g["period_months"])
            for g in clusters["groups"]
        ]
        source_keys = {key for key in source_key_values if key is not None}

        print("SOURCE_ROWS:", len(rows))
        print("UNIQUE_PROJECTS:", clusters["group_count"])
        print("EXACT_MATCHED:", counts["verified_exact_multi_document"])
        print("SINGLE_SOURCE:", counts["single_source_unmatched"])
        print("UNVERIFIED:", counts["unverified_source_row"])
        print("SAME_DOC_AMBIGUOUS:", counts["ambiguous_same_document_key"])
        print("SOURCE_VALID_UNIQUE_KEYS:", len(source_keys))
        print("SOURCE_UNKEYED_GROUPS:", source_key_values.count(None))
        print("SOURCE_ONGOING_END_GROUPS:", sum(
            g["period_months"][1] == "현재" for g in clusters["groups"]
        ))

        expected = (
            len(rows) == 66
            and clusters["group_count"] == 34
            and len(source_keys) == 34
            and counts["verified_exact_multi_document"] == 32
            and counts["single_source_unmatched"] == 2
            and counts["unverified_source_row"] == 0
            and counts["ambiguous_same_document_key"] == 0
        )
        if not expected:
            print("RESULT: REVIEW_REQUIRED")
            return

        confirmed = db.scalars(
            select(Project).where(
                Project.person_id == run.person_id,
                Project.deleted_at.is_(None),
            )
        ).all()
        confirmed_key_values = [
            _key(
                item.project_name, item.customer_name,
                item.start_date, item.end_date,
            )
            for item in confirmed
        ]
        confirmed_keys = {
            key for key in confirmed_key_values if key is not None
        }
        candidate = run.candidate_json or {}
        historical_projects = (
            candidate.get("projects", []) if isinstance(candidate, dict) else []
        )
        historical_key_values = [
            _key(
                item.get("project_name"), item.get("customer_name"),
                item.get("start_date"), item.get("end_date"),
            )
            for item in historical_projects
            if isinstance(item, dict)
        ]
        historical_keys = {
            key for key in historical_key_values if key is not None
        }

        print("CONFIRMED_PROJECT_ROWS:", len(confirmed))
        print("CONFIRMED_VALID_UNIQUE_KEYS:", len(confirmed_keys))
        print("CONFIRMED_UNKEYED_ROWS:", confirmed_key_values.count(None))
        print("SOURCE_MATCHED_CONFIRMED:", len(source_keys & confirmed_keys))
        print("SOURCE_ONLY:", len(source_keys - confirmed_keys))
        print("CONFIRMED_ONLY:", len(confirmed_keys - source_keys))
        print("HISTORICAL_CANDIDATE_ROWS:", len(historical_projects))
        print("HISTORICAL_CANDIDATE_VALID_KEYS:", len(historical_keys))
        print("HISTORICAL_UNKEYED_ROWS:", historical_key_values.count(None))
        print("SOURCE_MATCHED_HISTORICAL:", len(source_keys & historical_keys))

        # Diagnostics for the delta only. Never print names, dates or keys.
        historical_only = historical_keys - source_keys
        confirmed_only = confirmed_keys - source_keys
        source_only = source_keys - confirmed_keys
        print("HISTORICAL_ONLY:", len(historical_only))
        print("HISTORICAL_ONLY_IN_CONFIRMED:", len(historical_only & confirmed_keys))
        print("HISTORICAL_ONLY_NOT_CONFIRMED:",
              len(historical_only - confirmed_keys))
        print("SOURCE_ONLY_IN_HISTORICAL:",
              len(source_only & historical_keys))
        print("SOURCE_ONLY_SAME_NAME_CUSTOMER_CONFIRMED:", sum(
            any(key[:2] == other[:2] for other in confirmed_keys)
            for key in source_only
        ))
        print("SOURCE_ONLY_SAME_NAME_CONFIRMED:", sum(
            any(key[0] == other[0] for other in confirmed_keys)
            for key in source_only
        ))
        print("CONFIRMED_ONLY_IN_HISTORICAL:",
              len(confirmed_only & historical_keys))
        print("CONFIRMED_ONLY_NOT_HISTORICAL:",
              len(confirmed_only - historical_keys))
        print("CONFIRMED_FROM_SOURCE_RUN_ROWS:", sum(
            item.source_analysis_run_id == RUN for item in confirmed
        ))
        print("CONFIRMED_OTHER_SOURCE_RUN_ROWS:", sum(
            item.source_analysis_run_id != RUN for item in confirmed
        ))
        print("CONFIRMED_ONLY_SOURCE_RUN_ROWS:", sum(
            item.source_analysis_run_id == RUN
            and (key := _key(item.project_name, item.customer_name,
                             item.start_date, item.end_date)) is not None
            and key in confirmed_only
            for item in confirmed
        ))
        print("CONFIRMED_ONLY_OTHER_RUN_ROWS:", sum(
            item.source_analysis_run_id != RUN
            and (key := _key(item.project_name, item.customer_name,
                             item.start_date, item.end_date)) is not None
            and key in confirmed_only
            for item in confirmed
        ))

        # Pure in-memory Candidate+Diff dry run against the confirmed 52.
        # All private payloads remain in memory; only aggregate counts print.
        confirmed_snapshot = {
            "projects": [
                {
                    "id": str(item.id),
                    "project_name": item.project_name,
                    "customer_name": item.customer_name,
                    "start_date": (
                        item.start_date.isoformat() if item.start_date else None
                    ),
                    "end_date": item.end_date.isoformat() if item.end_date else None,
                    "duration_months": item.duration_months,
                    "responsibilities": item.responsibilities,
                    "project_summary": item.project_summary,
                    "jobs": [], "skills": [], "expertise": [],
                    "business_domains": [], "customer_types": [],
                }
                for item in confirmed
            ]
        }
        try:
            preview = assemble_native_direct(
                native_rows=rows,
                original_candidate_json=candidate,
                latest_confirmed_snapshot=confirmed_snapshot,
                page_texts={},  # Never invent unverifiable quote references.
            )
        except Exception as exc:
            reason = str(exc).split(":", 1)[0]
            if not reason.startswith(("NATIVE_V30_", "V30_")):
                reason = type(exc).__name__
            print("V30_DIFF_PREVIEW: FALLBACK", reason)
            print("RESULT: DIFF_REVIEW_REQUIRED")
            return

        checks = preview.diagnostics
        project_specs = [
            spec for spec in preview.specs if spec.entity_type == "PROJECT"
        ]
        by_candidate = {}
        for spec in project_specs:
            match = re.match(r"^projects\\[(\\d+)\\](?:\\.|$)",
                             spec.candidate_path or "")
            if match:
                by_candidate.setdefault(int(match.group(1)), []).append(spec)
        target_ids = {str(item.id) for item in confirmed}
        invalid_link_count = sum(
            spec.existing_target_id is not None
            and str(spec.existing_target_id) not in target_ids
            for spec in project_specs
        )
        linked_candidates = {}
        for index, specs in by_candidate.items():
            for spec in specs:
                if spec.existing_target_id is not None:
                    linked_candidates.setdefault(
                        str(spec.existing_target_id), set()
                    ).add(index)
        duplicate_link_targets = sum(
            len(indices) > 1 for indices in linked_candidates.values()
        )
        print("V30_CANDIDATE_PROJECTS:", len(preview.candidate.projects))
        print("V30_REPRESENTED_PROJECTS:",
              checks["represented_project_candidates"])
        print("V30_PROJECT_DIFF_COUNTS:", checks["project_diff_type_counts"])
        print("V30_UNMATCHED_HISTORICAL_AI:",
              checks["unmatched_original_ai_project_candidates"])
        print("V30_DIFF_UNKNOWN_TARGETS:", invalid_link_count)
        print("V30_DIFF_DUPLICATE_LINK_TARGETS:", duplicate_link_targets)
        print("V30_TITLE_VARIANT_REVIEW_COUNT:",
              checks["title_variant_auto_links_blocked"])
        print("V30_DEFAULT_GUARD_KEPT_ALL:",
              checks["default_guard_preserves_all_native_projects"])
        print("V30_NONPROJECT_CANDIDATE_UNCHANGED:",
              checks["nonproject_candidate_unchanged"])
        print("V30_AUTOMATIC_DELETIONS: 0")
        ok_preview = (
            len(preview.candidate.projects) == 34
            and checks["represented_project_candidates"] == 34
            and checks["unmatched_original_ai_project_candidates"] == 2
            and checks["default_guard_preserves_all_native_projects"]
            and checks["nonproject_candidate_unchanged"]
            and len(by_candidate) == 34
            and invalid_link_count == 0
            and duplicate_link_targets == 0
            and all(spec.change_type in {
                "SAME", "NEW", "UPDATE", "CONFLICT", "REVIEW"
            } for spec in project_specs)
        )
        print("V30_DIFF_PREVIEW:", "PASS" if ok_preview else "REVIEW_REQUIRED")
        print("RESULT:",
              "EXPECTED_34_PASS" if ok_preview else "DIFF_REVIEW_REQUIRED")
        print("DB_READ_ONLY: on")
        print("MODEL_CALLS: 0")
        print("===== TALENTSCOPE_V30_PPTX_SOURCE_COMPARE_COMPLETE =====")


if __name__ == "__main__":
    main()
