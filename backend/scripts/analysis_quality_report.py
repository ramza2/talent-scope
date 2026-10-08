#!/usr/bin/env python3
"""Read-only AnalysisRun quality report CLI.

Measures structure / evidence / duration of existing analysis runs.
Does not call LLM/VLM, mutate production data, or change analysis logic.

Usage (local Cloud Agent / host with DATABASE_URL):
  cd backend
  python scripts/analysis_quality_report.py --latest 5
  python scripts/analysis_quality_report.py --analysis-run-id <uuid>
  python scripts/analysis_quality_report.py --person-id <uuid> --latest 3
  python scripts/analysis_quality_report.py --latest 10 --format json --output /tmp/report.json

Server compose (production DATABASE_URL via api service; script bind-mounted RO):
  docker compose \\
    --env-file .env.server \\
    -f docker-compose.server.yml \\
    run --rm --no-deps \\
    -v "$PWD/backend:/workspace/backend:ro" \\
    --entrypoint python api \\
    /workspace/backend/scripts/analysis_quality_report.py \\
    --latest 5

  docker compose \\
    --env-file .env.server \\
    -f docker-compose.server.yml \\
    run --rm --no-deps \\
    -v "$PWD/backend:/workspace/backend:ro" \\
    --entrypoint python api \\
    /workspace/backend/scripts/analysis_quality_report.py \\
    --analysis-run-id <uuid> --format json

Notes:
  - Uses the api service production DATABASE_URL to read real AnalysisRun rows.
  - CLI itself is read-only (no DB write/commit).
  - Do not use the test service (POSTGRES_TEST_DB / recreated test DB).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument
from app.db.models.document import Document, DocumentGroup
from app.db.models.person import Person, PersonProfile
from app.db.session import SessionLocal
from app.modules.analysis.quality_report import (
    ENTITY_COUNT_KEYS,
    AggregateSummary,
    RunQualityReport,
    aggregate_reports,
    build_run_report,
    validate_selectors,
)

_DEFAULT_PERSON_LATEST = 5


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only AnalysisRun quality report (structure/evidence/duration).",
    )
    parser.add_argument(
        "--analysis-run-id",
        action="append",
        dest="analysis_run_ids",
        default=[],
        metavar="UUID",
        help="Analysis run id (repeatable).",
    )
    parser.add_argument(
        "--person-id",
        default=None,
        metavar="UUID",
        help="Limit to runs for one person (non-deleted). Uses --latest (default 5).",
    )
    parser.add_argument(
        "--latest",
        type=int,
        default=None,
        metavar="N",
        help="Take the N newest runs (global, or within --person-id).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "csv"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Write report to PATH instead of stdout.",
    )
    return parser.parse_args(argv)


def _parse_uuid_list(values: list[str]) -> list[UUID]:
    out: list[UUID] = []
    for raw in values:
        out.append(UUID(str(raw)))
    return out


def _visible_person_filter():
    return (
        Person.status != "DELETED",
        Person.deleted_at.is_(None),
    )


def select_run_ids(
    db: Session,
    *,
    analysis_run_ids: list[UUID],
    person_id: UUID | None,
    latest: int | None,
) -> list[UUID]:
    """Resolve selector combination into ordered run ids (newest first when limited)."""
    if analysis_run_ids:
        # Preserve caller order; still require visible person.
        ordered: list[UUID] = []
        for run_id in analysis_run_ids:
            row = db.execute(
                select(AnalysisRun.id)
                .join(Person, Person.id == AnalysisRun.person_id)
                .where(AnalysisRun.id == run_id, *_visible_person_filter())
            ).scalar_one_or_none()
            if row is not None:
                ordered.append(row)
        return ordered

    limit = latest if latest is not None else (
        _DEFAULT_PERSON_LATEST if person_id is not None else None
    )
    if limit is None:
        raise ValueError("Internal selector error: missing latest/person scope")

    stmt = (
        select(AnalysisRun.id)
        .join(Person, Person.id == AnalysisRun.person_id)
        .where(*_visible_person_filter())
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(limit)
    )
    if person_id is not None:
        stmt = stmt.where(AnalysisRun.person_id == person_id)
    return list(db.execute(stmt).scalars().all())


def _load_documents(db: Session, run_id: UUID) -> list[dict[str, Any]]:
    rows = db.execute(
        select(
            Document.id,
            Document.original_filename,
            DocumentGroup.document_type_code,
        )
        .join(
            AnalysisRunDocument,
            AnalysisRunDocument.document_id == Document.id,
        )
        .join(DocumentGroup, DocumentGroup.id == Document.document_group_id)
        .where(AnalysisRunDocument.analysis_run_id == run_id)
        .order_by(Document.original_filename.asc(), Document.id.asc())
    ).all()
    return [
        {
            "document_id": str(doc_id),
            "original_filename": filename,
            "document_type_code": doc_type,
        }
        for doc_id, filename, doc_type in rows
    ]


def _load_diff_rows(db: Session, run_id: UUID) -> list[tuple[str, str]]:
    rows = db.execute(
        select(AnalysisDiffItem.change_type, AnalysisDiffItem.review_status).where(
            AnalysisDiffItem.analysis_run_id == run_id
        )
    ).all()
    return [(str(change_type), str(review_status)) for change_type, review_status in rows]


def load_run_report(db: Session, run_id: UUID) -> RunQualityReport | None:
    row = db.execute(
        select(AnalysisRun, PersonProfile.name)
        .join(Person, Person.id == AnalysisRun.person_id)
        .outerjoin(PersonProfile, PersonProfile.person_id == Person.id)
        .where(AnalysisRun.id == run_id, *_visible_person_filter())
    ).one_or_none()
    if row is None:
        return None
    run, person_name = row
    documents = _load_documents(db, run.id)
    diff_rows = _load_diff_rows(db, run.id)
    return build_run_report(
        analysis_run_id=run.id,
        person_id=run.person_id,
        person_name=person_name,
        status=run.status,
        candidate_json=run.candidate_json if isinstance(run.candidate_json, dict) else {},
        documents=documents,
        prompt_version=run.prompt_version,
        schema_version=run.schema_version,
        llm_model=run.llm_model,
        vlm_model=run.vlm_model,
        started_at=run.started_at,
        completed_at=run.completed_at,
        diff_rows=diff_rows,
        error_message=run.error_message,
    )


def format_text(
    reports: list[RunQualityReport], aggregate: AggregateSummary
) -> str:
    lines: list[str] = []
    lines.append("Analysis quality report (read-only; not ground-truth accuracy)")
    lines.append("=" * 72)
    for report in reports:
        lines.append("")
        lines.append(f"run {report.analysis_run_id}")
        lines.append(f"  person_id          {report.person_id}")
        lines.append(f"  person_name        {report.person_name or '—'}")
        lines.append(f"  status             {report.status}")
        lines.append(f"  documents          {report.document_count}")
        if report.documents:
            for doc in report.documents:
                lines.append(
                    "    - "
                    f"{doc.get('original_filename') or '—'} "
                    f"[{doc.get('document_type_code') or '—'}] "
                    f"({doc.get('document_id')})"
                )
        lines.append(f"  prompt_version     {report.prompt_version or '—'}")
        lines.append(f"  schema_version     {report.schema_version or '—'}")
        lines.append(f"  llm_model          {report.llm_model or '—'}")
        lines.append(f"  vlm_model          {report.vlm_model or '—'}")
        lines.append(f"  started_at         {report.started_at or '—'}")
        lines.append(f"  completed_at       {report.completed_at or '—'}")
        duration = (
            f"{report.duration_seconds:.3f}s"
            if report.duration_seconds is not None
            else "—"
        )
        lines.append(f"  duration_seconds   {duration}")
        if report.error_message:
            lines.append(f"  error_message      {report.error_message}")
        lines.append("  entity_counts")
        for key in ENTITY_COUNT_KEYS:
            lines.append(f"    {key:<16} {report.entity_counts.get(key, 0)}")
        ev = report.evidence
        cov = (
            f"{ev.evidence_coverage_pct:.2f}%"
            if ev.evidence_coverage_pct is not None
            else "—"
        )
        lines.append("  evidence")
        lines.append(f"    eligible         {ev.evidence_eligible_count}")
        lines.append(f"    with_source_refs {ev.with_source_refs_count}")
        lines.append(f"    source_ref_count {ev.source_ref_count}")
        lines.append(f"    coverage_pct     {cov}")
        lines.append(f"    refs_document_id {ev.refs_with_document_id}")
        lines.append(f"    refs_quote_text  {ev.refs_with_quote_text}")
        lines.append(f"    refs_page_no     {ev.refs_with_page_no}")
        lines.append(f"    valid_doc_refs   {ev.valid_document_ref_count}")
        lines.append(f"    invalid_doc_refs {ev.invalid_document_ref_count}")
        d = report.diffs
        lines.append(
            "  diffs "
            f"SAME={d.same} NEW={d.new} UPDATE={d.update} "
            f"CONFLICT={d.conflict} REVIEW={d.review} PENDING={d.pending}"
        )

    lines.append("")
    lines.append("Aggregate")
    lines.append("-" * 72)
    lines.append(f"  run_count                 {aggregate.run_count}")
    lines.append(f"  status_counts             {aggregate.status_counts}")
    avg = (
        f"{aggregate.avg_duration_seconds:.3f}"
        if aggregate.avg_duration_seconds is not None
        else "—"
    )
    lines.append(f"  avg_duration_seconds      {avg}")
    lines.append(f"  total_document_count      {aggregate.total_document_count}")
    lines.append(f"  entity_count_totals       {aggregate.entity_count_totals}")
    cov = (
        f"{aggregate.evidence_coverage_pct:.2f}%"
        if aggregate.evidence_coverage_pct is not None
        else "—"
    )
    lines.append(
        "  evidence_coverage_pct     "
        f"{cov} "
        f"({aggregate.with_source_refs_count}/"
        f"{aggregate.evidence_eligible_count})"
    )
    lines.append(
        f"  invalid_document_ref_count {aggregate.invalid_document_ref_count}"
    )
    lines.append(f"  diff_totals               {aggregate.diff_totals}")
    lines.append("")
    return "\n".join(lines)


def format_json(
    reports: list[RunQualityReport], aggregate: AggregateSummary
) -> str:
    payload = {
        "reports": [report.to_dict() for report in reports],
        "aggregate": aggregate.to_dict(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def format_csv(
    reports: list[RunQualityReport], aggregate: AggregateSummary
) -> str:
    buf = StringIO()
    fieldnames = [
        "analysis_run_id",
        "person_id",
        "person_name",
        "status",
        "document_count",
        "prompt_version",
        "schema_version",
        "llm_model",
        "vlm_model",
        "started_at",
        "completed_at",
        "duration_seconds",
        *[f"entity_{key}" for key in ENTITY_COUNT_KEYS],
        "evidence_eligible_count",
        "with_source_refs_count",
        "source_ref_count",
        "evidence_coverage_pct",
        "refs_with_document_id",
        "refs_with_quote_text",
        "refs_with_page_no",
        "valid_document_ref_count",
        "invalid_document_ref_count",
        "diff_same",
        "diff_new",
        "diff_update",
        "diff_conflict",
        "diff_review",
        "diff_pending",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for report in reports:
        row: dict[str, Any] = {
            "analysis_run_id": report.analysis_run_id,
            "person_id": report.person_id,
            "person_name": report.person_name,
            "status": report.status,
            "document_count": report.document_count,
            "prompt_version": report.prompt_version,
            "schema_version": report.schema_version,
            "llm_model": report.llm_model,
            "vlm_model": report.vlm_model,
            "started_at": report.started_at,
            "completed_at": report.completed_at,
            "duration_seconds": report.duration_seconds,
            "evidence_eligible_count": report.evidence.evidence_eligible_count,
            "with_source_refs_count": report.evidence.with_source_refs_count,
            "source_ref_count": report.evidence.source_ref_count,
            "evidence_coverage_pct": report.evidence.evidence_coverage_pct,
            "refs_with_document_id": report.evidence.refs_with_document_id,
            "refs_with_quote_text": report.evidence.refs_with_quote_text,
            "refs_with_page_no": report.evidence.refs_with_page_no,
            "valid_document_ref_count": report.evidence.valid_document_ref_count,
            "invalid_document_ref_count": report.evidence.invalid_document_ref_count,
            "diff_same": report.diffs.same,
            "diff_new": report.diffs.new,
            "diff_update": report.diffs.update,
            "diff_conflict": report.diffs.conflict,
            "diff_review": report.diffs.review,
            "diff_pending": report.diffs.pending,
        }
        for key in ENTITY_COUNT_KEYS:
            row[f"entity_{key}"] = report.entity_counts.get(key, 0)
        writer.writerow(row)
    # Aggregate as a final synthetic row for quick spreadsheet use.
    agg_row: dict[str, Any] = {name: "" for name in fieldnames}
    agg_row["analysis_run_id"] = "__AGGREGATE__"
    agg_row["status"] = json.dumps(aggregate.status_counts, ensure_ascii=False)
    agg_row["document_count"] = aggregate.total_document_count
    agg_row["duration_seconds"] = aggregate.avg_duration_seconds
    for key in ENTITY_COUNT_KEYS:
        agg_row[f"entity_{key}"] = aggregate.entity_count_totals.get(key, 0)
    agg_row["evidence_eligible_count"] = aggregate.evidence_eligible_count
    agg_row["with_source_refs_count"] = aggregate.with_source_refs_count
    agg_row["source_ref_count"] = aggregate.source_ref_count
    agg_row["evidence_coverage_pct"] = aggregate.evidence_coverage_pct
    agg_row["refs_with_document_id"] = aggregate.refs_with_document_id
    agg_row["refs_with_quote_text"] = aggregate.refs_with_quote_text
    agg_row["refs_with_page_no"] = aggregate.refs_with_page_no
    agg_row["valid_document_ref_count"] = aggregate.valid_document_ref_count
    agg_row["invalid_document_ref_count"] = aggregate.invalid_document_ref_count
    for key in ("same", "new", "update", "conflict", "review", "pending"):
        agg_row[f"diff_{key}"] = aggregate.diff_totals.get(key, 0)
    writer.writerow(agg_row)
    return buf.getvalue()


def render(
    reports: list[RunQualityReport],
    aggregate: AggregateSummary,
    fmt: str,
) -> str:
    if fmt == "json":
        return format_json(reports, aggregate)
    if fmt == "csv":
        return format_csv(reports, aggregate)
    return format_text(reports, aggregate)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        run_ids = _parse_uuid_list(args.analysis_run_ids)
        person_id = UUID(args.person_id) if args.person_id else None
    except ValueError as exc:
        print(f"Invalid UUID: {exc}", file=sys.stderr)
        return 2

    error = validate_selectors(
        analysis_run_ids=run_ids,
        person_id=person_id,
        latest=args.latest,
    )
    if error:
        print(error, file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        selected = select_run_ids(
            db,
            analysis_run_ids=run_ids,
            person_id=person_id,
            latest=args.latest,
        )
        reports: list[RunQualityReport] = []
        for run_id in selected:
            report = load_run_report(db, run_id)
            if report is not None:
                reports.append(report)
        # Explicitly avoid committing; roll back any incidental transaction state.
        db.rollback()
    finally:
        db.close()

    if run_ids and not reports:
        print("No matching visible analysis runs found.", file=sys.stderr)
        return 1

    aggregate = aggregate_reports(reports)
    text = render(reports, aggregate, args.format)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
