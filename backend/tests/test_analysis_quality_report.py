"""Focused unit tests for AnalysisRun quality report metrics (no DB/AI)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.modules.analysis.quality_report import (
    aggregate_reports,
    build_run_report,
    collect_evidence_metrics,
    count_entities,
    duration_seconds,
    summarize_diffs,
    validate_selectors,
)


def test_count_entities_canonical_keys() -> None:
    doc_id = str(uuid4())
    candidate = {
        "jobs": [{"raw_value": "AI개발자", "source_refs": []}],
        "skills": [
            {"raw_value": "Python", "source_refs": [{"document_id": doc_id}]},
            {"raw_value": "Java"},
        ],
        "expertise": [{"raw_value": "RAG"}],
        "employment_history": [{"company_name": "Acme"}],
        "education": [{"school_name": "Univ"}],
        "certifications": [{"certification_name": "OCP"}],
        "projects": [{"project_name": "P1"}, "skip-me", {"project_name": "P2"}],
    }
    counts = count_entities(candidate)
    assert counts == {
        "jobs": 1,
        "skills": 2,
        "expertise": 1,
        "projects": 2,
        "employment": 1,
        "education": 1,
        "certifications": 1,
    }


def test_count_entities_empty_or_malformed() -> None:
    assert count_entities({}) == {
        "jobs": 0,
        "skills": 0,
        "expertise": 0,
        "projects": 0,
        "employment": 0,
        "education": 0,
        "certifications": 0,
    }
    assert count_entities(None)["jobs"] == 0
    assert count_entities("bad")["skills"] == 0
    assert count_entities({"jobs": "x", "skills": [1, None]})["skills"] == 0


def test_evidence_without_source_refs() -> None:
    candidate = {
        "profile": {"name": "홍길동"},
        "jobs": [{"raw_value": "PL"}],
        "skills": [{"raw_value": "Python"}],
    }
    metrics = collect_evidence_metrics(candidate, valid_document_ids=set())
    assert metrics.evidence_eligible_count == 3  # name + job + skill
    assert metrics.with_source_refs_count == 0
    assert metrics.source_ref_count == 0
    assert metrics.evidence_coverage_pct == 0.0


def test_evidence_single_and_multiple_source_refs() -> None:
    d1 = str(uuid4())
    d2 = str(uuid4())
    candidate = {
        "skills": [
            {
                "raw_value": "Python",
                "source_refs": [
                    {"document_id": d1, "quote_text": "Python 5년", "page_no": 2}
                ],
            },
            {
                "raw_value": "Java",
                "source_refs": [
                    {"document_id": d1, "quote_text": "Java"},
                    {"document_id": d2, "quote_text": "Spring"},
                ],
            },
        ]
    }
    metrics = collect_evidence_metrics(
        candidate, valid_document_ids={d1, d2}
    )
    assert metrics.evidence_eligible_count == 2
    assert metrics.with_source_refs_count == 2
    assert metrics.source_ref_count == 3
    assert metrics.refs_with_document_id == 3
    assert metrics.refs_with_quote_text == 3
    assert metrics.refs_with_page_no == 1
    assert metrics.valid_document_ref_count == 3
    assert metrics.invalid_document_ref_count == 0
    assert metrics.evidence_coverage_pct == 100.0


def test_evidence_valid_and_invalid_document_ids() -> None:
    linked = str(uuid4())
    other = str(uuid4())
    candidate = {
        "projects": [
            {
                "project_name": "A",
                "source_refs": [
                    {"document_id": linked, "quote_text": "ok"},
                    {"document_id": other, "quote_text": "bad"},
                    {"quote_text": "no-doc"},
                ],
            }
        ]
    }
    metrics = collect_evidence_metrics(
        candidate, valid_document_ids={linked}
    )
    assert metrics.source_ref_count == 3
    assert metrics.refs_with_document_id == 2
    assert metrics.valid_document_ref_count == 1
    assert metrics.invalid_document_ref_count == 1


def test_malformed_source_refs_do_not_crash() -> None:
    candidate = {
        "profile": {
            "name": "홍길동",
            "source_refs": "not-a-map",
        },
        "jobs": [
            {"raw_value": "PL", "source_refs": "oops"},
            {"raw_value": "AI", "source_refs": [{"document_id": 123}, None, "x"]},
            "not-a-dict",
        ],
        "projects": [
            {
                "project_name": "P",
                "source_refs": [{"document_id": str(uuid4()), "quote_text": "q"}],
                "skills": "bad",
                "expertise": [{"raw_value": "RAG", "source_refs": None}],
            }
        ],
    }
    metrics = collect_evidence_metrics(candidate, valid_document_ids=set())
    assert metrics.evidence_eligible_count >= 4
    assert metrics.source_ref_count >= 1
    assert metrics.invalid_document_ref_count >= 1


def test_page_no_absent_is_allowed() -> None:
    doc_id = str(uuid4())
    candidate = {
        "skills": [
            {
                "raw_value": "Python",
                "source_refs": [{"document_id": doc_id, "quote_text": "Python"}],
            }
        ]
    }
    metrics = collect_evidence_metrics(
        candidate, valid_document_ids={doc_id}
    )
    assert metrics.refs_with_page_no == 0
    assert metrics.with_source_refs_count == 1
    assert metrics.evidence_coverage_pct == 100.0


def test_profile_scalar_denominator_only_filled_fields() -> None:
    doc_id = str(uuid4())
    candidate = {
        "profile": {
            "name": "홍길동",
            "phone": None,
            "email": "",
            "technical_grade": "ADVANCED",
            "source_refs": {
                "name": [{"document_id": doc_id, "quote_text": "홍길동"}],
            },
        }
    }
    metrics = collect_evidence_metrics(
        candidate, valid_document_ids={doc_id}
    )
    # name + technical_grade only
    assert metrics.evidence_eligible_count == 2
    assert metrics.with_source_refs_count == 1
    assert metrics.evidence_coverage_pct == 50.0


def test_duration_seconds() -> None:
    start = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=12.5)
    assert duration_seconds(start, end) == 12.5
    assert duration_seconds(None, end) is None
    assert duration_seconds(start, None) is None
    assert duration_seconds(None, None) is None


def test_aggregate_coverage_uses_sums_not_pct_mean() -> None:
    d1 = str(uuid4())
    # run1 coverage 100% (1/1), run2 coverage 0% (0/3)
    # simple mean of pct = 50%; summed coverage = 1/4 = 25%
    r1 = build_run_report(
        analysis_run_id=uuid4(),
        person_id=uuid4(),
        person_name="A",
        status="REVIEWING",
        candidate_json={
            "skills": [
                {"raw_value": "Python", "source_refs": [{"document_id": d1}]},
            ]
        },
        documents=[{"document_id": d1}],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC),
    )
    r2 = build_run_report(
        analysis_run_id=uuid4(),
        person_id=uuid4(),
        person_name="B",
        status="CONFIRMED",
        candidate_json={
            "skills": [
                {"raw_value": "Go"},
                {"raw_value": "Rust"},
                {"raw_value": "C"},
            ]
        },
    )
    agg = aggregate_reports([r1, r2])
    assert agg.run_count == 2
    assert agg.status_counts == {"REVIEWING": 1, "CONFIRMED": 1}
    assert agg.evidence_eligible_count == 4
    assert agg.with_source_refs_count == 1
    assert agg.evidence_coverage_pct == 25.0
    assert agg.avg_duration_seconds == 10.0
    assert agg.entity_count_totals["skills"] == 4
    assert agg.total_document_count == 1


def test_empty_candidate_and_failed_run_report() -> None:
    report = build_run_report(
        analysis_run_id=uuid4(),
        person_id=uuid4(),
        person_name="실패자",
        status="FAILED",
        candidate_json={},
        documents=[],
        error_message="LLM timeout",
        diff_rows=[],
    )
    assert report.entity_counts["projects"] == 0
    assert report.evidence.evidence_eligible_count == 0
    assert report.evidence.evidence_coverage_pct is None
    assert report.diffs.pending == 0
    assert report.error_message == "LLM timeout"
    assert report.duration_seconds is None


def test_diff_summary_and_pending() -> None:
    summary = summarize_diffs(
        [
            ("NEW", "PENDING"),
            ("UPDATE", "ACCEPTED"),
            ("SAME", "PENDING"),
            ("CONFLICT", "REJECTED"),
            ("REVIEW", "PENDING"),
            ("UNKNOWN", "PENDING"),
        ]
    )
    assert summary.new == 1
    assert summary.update == 1
    assert summary.same == 1
    assert summary.conflict == 1
    assert summary.review == 1
    assert summary.pending == 4


def test_validate_selectors() -> None:
    assert validate_selectors(
        analysis_run_ids=[], person_id=None, latest=None
    )
    assert (
        validate_selectors(
            analysis_run_ids=[], person_id=None, latest=0
        )
        is not None
    )
    assert (
        validate_selectors(
            analysis_run_ids=[uuid4()], person_id=None, latest=None
        )
        is None
    )
    assert (
        validate_selectors(
            analysis_run_ids=[], person_id=uuid4(), latest=None
        )
        is None
    )
    assert (
        validate_selectors(
            analysis_run_ids=[], person_id=None, latest=3
        )
        is None
    )
