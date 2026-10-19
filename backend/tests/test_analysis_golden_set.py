from __future__ import annotations

from uuid import uuid4

from app.modules.analysis.golden_set import (
    GOLDEN_SCHEMA_VERSION,
    aggregate_evaluations,
    evaluate_candidate,
    validate_golden_set,
)


def _case(expected):
    return {
        "case_id": "case-1",
        "person_id": str(uuid4()),
        "analysis_run_id": str(uuid4()),
        "expected": expected,
    }


def test_root_code_precision_recall_and_evidence():
    case = _case({"jobs": ["JOB-A", "JOB-B"], "skills": ["TECH-X"]})
    out = evaluate_candidate(
        case=case,
        candidate_json={
            "jobs": [
                {"code": "JOB-A", "source_refs": [{"quote_text": "A"}]},
                {"code": "JOB-C", "source_refs": [{"quote_text": "C"}]},
            ],
            "skills": [{"code": "TECH-X", "source_refs": []}],
        },
        analysis_run_id=case["analysis_run_id"],
    )
    assert out.root_scores["jobs"].matched_count == 1
    assert out.root_scores["jobs"].false_positive_count == 1
    assert out.root_scores["jobs"].false_negative_count == 1
    assert out.root_scores["jobs"].precision == 50.0
    assert out.root_scores["jobs"].recall == 50.0
    assert out.missing["jobs"] == ["JOB-B"]
    assert out.unexpected["jobs"] == ["JOB-C"]
    assert out.evidence.expected_count == 3
    assert out.evidence.with_source_refs_count == 1
    assert out.evidence.coverage_pct == 33.33


def test_omitted_root_key_is_not_scored_but_empty_list_asserts_none():
    case = _case({"jobs": []})
    out = evaluate_candidate(
        case=case,
        candidate_json={
            "jobs": [{"code": "JOB-X"}],
            "skills": [{"code": "TECH-X"}],
        },
        analysis_run_id=case["analysis_run_id"],
    )
    assert set(out.root_scores) == {"jobs"}
    assert out.root_scores["jobs"].false_positive_count == 1
    assert "skills" not in out.unexpected


def test_project_match_whitespace_and_expected_month_precision():
    case = _case(
        {
            "projects": [
                {
                    "project_name": "범정부 정보자원 구축",
                    "customer_name": "정부 통합 전산센터",
                    "start_date": "2019.07",
                    "end_date": "2020.01",
                }
            ]
        }
    )
    out = evaluate_candidate(
        case=case,
        candidate_json={
            "projects": [
                {
                    "project_name": "범정부정보자원구축",
                    "customer_name": "정부통합전산센터",
                    "start_date": "2019-07-01",
                    "end_date": "2020-01-31",
                    "source_refs": [{"quote_text": "project"}],
                }
            ]
        },
        analysis_run_id=case["analysis_run_id"],
    )
    assert out.project_score.matched_count == 1
    assert out.project_score.false_positive_count == 0
    assert out.project_score.false_negative_count == 0
    assert out.evidence.coverage_pct == 100.0


def test_project_nested_relations_are_scored_per_matched_project():
    case = _case(
        {
            "projects": [
                {
                    "project_name": "P1",
                    "jobs": ["JOB-OPS"],
                    "expertise": ["EXP-INFRA"],
                }
            ]
        }
    )
    out = evaluate_candidate(
        case=case,
        candidate_json={
            "projects": [
                {
                    "project_name": "P1",
                    "source_refs": [{"quote_text": "P1"}],
                    "jobs": [
                        {"code": "JOB-OPS", "source_refs": [{"quote_text": "ops"}]},
                        {
                            "code": "JOB-EXTRA",
                            "source_refs": [{"quote_text": "extra"}],
                        },
                    ],
                    "expertise": [{"code": "EXP-INFRA", "source_refs": []}],
                }
            ]
        },
        analysis_run_id=case["analysis_run_id"],
    )
    assert out.project_relation_scores["jobs"].matched_count == 1
    assert out.project_relation_scores["jobs"].false_positive_count == 1
    assert out.project_relation_scores["expertise"].matched_count == 1
    assert out.evidence.expected_count == 3
    assert out.evidence.with_source_refs_count == 2
    assert out.evidence.coverage_pct == 66.67


def test_missing_project_and_unexpected_project_reported():
    case = _case({"projects": [{"project_name": "Expected"}]})
    out = evaluate_candidate(
        case=case,
        candidate_json={"projects": [{"project_name": "Actual"}]},
        analysis_run_id=case["analysis_run_id"],
    )
    assert out.project_score.matched_count == 0
    assert out.project_score.false_positive_count == 1
    assert out.project_score.false_negative_count == 1
    assert out.missing["projects"][0].startswith("Expected")
    assert out.unexpected["projects"][0].startswith("Actual")
    assert out.evidence.expected_count == 1
    assert out.evidence.with_source_refs_count == 0


def test_aggregate_recomputes_metrics_from_counts():
    c1 = _case({"jobs": ["JOB-A"]})
    c2 = {
        **_case({"jobs": ["JOB-A", "JOB-B"]}),
        "case_id": "case-2",
    }
    r1 = evaluate_candidate(
        case=c1,
        candidate_json={
            "jobs": [
                {"code": "JOB-A", "source_refs": [{"quote_text": "a"}]}
            ]
        },
        analysis_run_id=c1["analysis_run_id"],
    )
    r2 = evaluate_candidate(
        case=c2,
        candidate_json={"jobs": [{"code": "JOB-A"}]},
        analysis_run_id=c2["analysis_run_id"],
    )
    agg = aggregate_evaluations([r1, r2])
    assert agg.case_count == 2
    assert agg.root_scores["jobs"].expected_count == 3
    assert agg.root_scores["jobs"].actual_count == 2
    assert agg.root_scores["jobs"].matched_count == 2
    assert agg.root_scores["jobs"].precision == 100.0
    assert agg.root_scores["jobs"].recall == 66.67
    assert agg.evidence.expected_count == 3
    assert agg.evidence.with_source_refs_count == 1
    assert agg.evidence.coverage_pct == 33.33


def test_validate_golden_set_accepts_partial_expectations():
    payload = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "cases": [
            {
                "case_id": "a",
                "person_id": str(uuid4()),
                "expected": {
                    "skills": ["TECH-PYTHON"],
                    "projects": [{"project_name": "P", "jobs": []}],
                },
            }
        ],
    }
    assert validate_golden_set(payload) is payload


def test_validate_golden_set_rejects_duplicate_codes():
    payload = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "cases": [
            {
                "case_id": "a",
                "person_id": str(uuid4()),
                "expected": {"skills": ["TECH-X", "tech-x"]},
            }
        ],
    }
    try:
        validate_golden_set(payload)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_missing_project_relations_still_count_toward_expected_evidence():
    case = _case(
        {
            "projects": [
                {
                    "project_name": "Missing",
                    "jobs": ["JOB-A"],
                    "expertise": ["EXP-A"],
                }
            ]
        }
    )
    out = evaluate_candidate(
        case=case,
        candidate_json={"projects": []},
        analysis_run_id=case["analysis_run_id"],
    )
    assert out.evidence.expected_count == 3
    assert out.evidence.with_source_refs_count == 0
    assert out.evidence.coverage_pct == 0.0
