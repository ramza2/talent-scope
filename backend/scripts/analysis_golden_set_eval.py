#!/usr/bin/env python3
"""Read-only Golden Set accuracy evaluator for existing AnalysisRun candidates.

The Golden Set file contains expected JOB/TECH/EXP codes, PROJECT identities,
optional project relation codes, and implied source_refs requirements.
This tool never triggers AI, updates AnalysisRun, or confirms profile data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.models.person import Person, PersonProfile
from app.db.session import SessionLocal
from app.modules.analysis.golden_set import (
    AggregateEvaluation,
    CaseEvaluation,
    aggregate_evaluations,
    evaluate_candidate,
    validate_golden_set,
)

_SUCCESS_STATUSES = ("REVIEWING", "CONFIRMED")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Golden Set accuracy evaluation for AnalysisRun candidates.",
    )
    parser.add_argument(
        "--golden-set",
        required=True,
        metavar="PATH",
        help="Golden Set JSON file.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
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


def _visible_person_filter():
    return (Person.status != "DELETED", Person.deleted_at.is_(None))


def _load_case_run(
    db: Session,
    *,
    person_id: UUID,
    analysis_run_id: UUID | None,
) -> tuple[AnalysisRun, str | None] | None:
    stmt = (
        select(AnalysisRun, PersonProfile.name)
        .join(Person, Person.id == AnalysisRun.person_id)
        .outerjoin(PersonProfile, PersonProfile.person_id == Person.id)
        .where(AnalysisRun.person_id == person_id, *_visible_person_filter())
    )
    if analysis_run_id is not None:
        stmt = stmt.where(AnalysisRun.id == analysis_run_id)
    else:
        stmt = (
            stmt.where(AnalysisRun.status.in_(_SUCCESS_STATUSES))
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
            .limit(1)
        )
    return db.execute(stmt).one_or_none()


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read golden set: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid golden set JSON: {exc}") from exc
    return validate_golden_set(payload)


def evaluate_cases(db: Session, payload: dict[str, Any]) -> list[CaseEvaluation]:
    results: list[CaseEvaluation] = []
    for case in payload["cases"]:
        person_id = UUID(str(case["person_id"]))
        raw_run_id = case.get("analysis_run_id")
        requested_run_id = UUID(str(raw_run_id)) if raw_run_id else None
        loaded = _load_case_run(
            db,
            person_id=person_id,
            analysis_run_id=requested_run_id,
        )
        if loaded is None:
            result = evaluate_candidate(
                case=case,
                candidate_json={},
                analysis_run_id=str(requested_run_id) if requested_run_id else None,
                run_status="NO_RUN",
            )
            result.notes.append(
                "requested run not found"
                if requested_run_id
                else "no REVIEWING/CONFIRMED run found"
            )
            results.append(result)
            continue
        run, person_name = loaded
        result = evaluate_candidate(
            case=case,
            candidate_json=run.candidate_json if isinstance(run.candidate_json, dict) else {},
            analysis_run_id=str(run.id),
            person_name=person_name,
            run_status=run.status,
        )
        results.append(result)
    return results


def _score_line(name: str, score) -> str:
    precision = "—" if score.precision is None else f"{score.precision:.2f}%"
    recall = "—" if score.recall is None else f"{score.recall:.2f}%"
    f1 = "—" if score.f1 is None else f"{score.f1:.2f}%"
    return (
        f"{name:<24} matched={score.matched_count}/{score.expected_count} "
        f"actual={score.actual_count} FP={score.false_positive_count} "
        f"FN={score.false_negative_count} P={precision} R={recall} F1={f1}"
    )


def format_text(cases: list[CaseEvaluation], aggregate: AggregateEvaluation) -> str:
    lines = [
        "Analysis Golden Set evaluation (read-only)",
        "=" * 88,
    ]
    for case in cases:
        lines.extend(
            [
                "",
                f"case {case.case_id}",
                f"  person_id          {case.person_id}",
                f"  person_name        {case.person_name or '—'}",
                f"  analysis_run_id    {case.analysis_run_id or '—'}",
                f"  run_status         {case.run_status or '—'}",
            ]
        )
        for key, score in case.root_scores.items():
            lines.append("  " + _score_line(key, score))
        if case.project_score.expected_count or case.project_score.actual_count:
            lines.append("  " + _score_line("projects", case.project_score))
        for key, score in case.project_relation_scores.items():
            lines.append("  " + _score_line(f"project_{key}", score))
        ev = case.evidence
        coverage = "—" if ev.coverage_pct is None else f"{ev.coverage_pct:.2f}%"
        lines.append(
            f"  evidence                 {ev.with_source_refs_count}/"
            f"{ev.expected_count} ({coverage})"
        )
        for key, values in case.missing.items():
            if values:
                lines.append(f"  missing {key}: {', '.join(values)}")
        for key, values in case.unexpected.items():
            if values:
                lines.append(f"  unexpected {key}: {', '.join(values)}")
        for note in case.notes:
            lines.append(f"  note: {note}")

    lines.extend(
        [
            "",
            "Aggregate",
            "-" * 88,
            f"  case_count               {aggregate.case_count}",
        ]
    )
    for key, score in aggregate.root_scores.items():
        lines.append("  " + _score_line(key, score))
    if aggregate.project_score.expected_count or aggregate.project_score.actual_count:
        lines.append("  " + _score_line("projects", aggregate.project_score))
    for key, score in aggregate.project_relation_scores.items():
        lines.append("  " + _score_line(f"project_{key}", score))
    ev = aggregate.evidence
    coverage = "—" if ev.coverage_pct is None else f"{ev.coverage_pct:.2f}%"
    lines.append(
        f"  evidence                 {ev.with_source_refs_count}/"
        f"{ev.expected_count} ({coverage})"
    )
    lines.append("")
    return "\n".join(lines)


def format_json(cases: list[CaseEvaluation], aggregate: AggregateEvaluation) -> str:
    return json.dumps(
        {
            "cases": [case.to_dict() for case in cases],
            "aggregate": aggregate.to_dict(),
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = _load_payload(Path(args.golden_set))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        cases = evaluate_cases(db, payload)
        db.rollback()
    finally:
        db.close()

    aggregate = aggregate_evaluations(cases)
    text = (
        format_json(cases, aggregate)
        if args.format == "json"
        else format_text(cases, aggregate)
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"Wrote {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
