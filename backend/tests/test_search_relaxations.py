"""Deterministic search relaxation suggestions for zero-hit queries."""

from __future__ import annotations

import copy

from app.modules.search.query_schemas import SearchPeopleRequest
from app.modules.search.query_service import build_search_relaxations


def _req(**kwargs) -> SearchPeopleRequest:
    return SearchPeopleRequest.model_validate(kwargs)


def test_suggest_relaxations_false_returns_empty_even_when_zero_rules_match() -> None:
    request = _req(
        suggest_relaxations=False,
        skill_match_mode="ALL",
        required={"skills": ["TECH-A", "TECH-B"]},
    )
    total = 0
    relaxations = (
        build_search_relaxations(request)
        if request.suggest_relaxations and total == 0
        else []
    )
    assert relaxations == []
    # Builder itself still produces candidates when invoked directly.
    assert build_search_relaxations(request)

def test_nonzero_total_skips_relaxations_contract() -> None:
    """Document service gate: total > 0 yields no relaxations regardless of flag."""
    request = _req(
        suggest_relaxations=True,
        skill_match_mode="ALL",
        required={"skills": ["TECH-A", "TECH-B"]},
    )
    total = 3
    relaxations = (
        build_search_relaxations(request)
        if request.suggest_relaxations and total == 0
        else []
    )
    assert relaxations == []


def test_skill_match_all_to_any() -> None:
    request = _req(
        skill_match_mode="ALL",
        required={"skills": ["TECH-A", "TECH-B"]},
    )
    items = build_search_relaxations(request)
    assert items
    assert items[0].id == "skill_match_any"
    assert items[0].suggested_query["skill_match_mode"] == "ANY"
    assert items[0].suggested_query["required"]["skills"] == ["TECH-A", "TECH-B"]


def test_career_min_months_relaxed_by_12() -> None:
    request = _req(required={"career": {"min_months": 60, "max_months": 120}})
    items = build_search_relaxations(request)
    assert items
    assert items[0].id == "career_min_minus_12"
    career = items[0].suggested_query["required"]["career"]
    assert career["min_months"] == 48
    assert career["max_months"] == 120
    assert "60" in items[0].label and "48" in items[0].label


def test_grade_and_required_code_to_preferred() -> None:
    request = _req(
        required={
            "grade": {"values": ["EXPERT"]},
            "jobs": ["JOB-AI"],
            "skills": ["TECH-PY"],
        }
    )
    items = build_search_relaxations(request)
    ids = [i.id for i in items]
    assert "drop_required_grade" in ids
    assert any(i.startswith("required_to_preferred_jobs_") for i in ids)
    grade_item = next(i for i in items if i.id == "drop_required_grade")
    assert grade_item.suggested_query["required"]["grade"] is None
    job_item = next(i for i in items if i.id.startswith("required_to_preferred_jobs_"))
    assert job_item.suggested_query["required"]["jobs"] == []
    assert "JOB-AI" in job_item.suggested_query["preferred"]["jobs"]


def test_does_not_mutate_original_request() -> None:
    request = _req(
        skill_match_mode="ALL",
        required={
            "skills": ["TECH-A", "TECH-B"],
            "career": {"min_months": 36},
            "grade": {"values": ["ADVANCED"]},
            "jobs": ["JOB-X"],
            "affiliations": ["ACME"],
        },
        preferred={"jobs": []},
        keyword_query="python",
    )
    before = copy.deepcopy(request.model_dump())
    items = build_search_relaxations(request)
    assert items
    assert request.model_dump() == before
    assert request.skill_match_mode == "ALL"
    assert request.required.skills == ["TECH-A", "TECH-B"]
    assert request.required.career is not None
    assert request.required.career.min_months == 36
    assert request.keyword_query == "python"


def test_max_three_relaxations_and_no_duplicate_suggested_query() -> None:
    request = _req(
        skill_match_mode="ALL",
        required={
            "skills": ["TECH-A", "TECH-B"],
            "career": {"min_months": 24},
            "grade": {"values": ["EXPERT"]},
            "jobs": ["JOB-1"],
            "expertise": ["EXP-1"],
            "affiliations": ["Corp"],
        },
        keyword_query="kw",
    )
    items = build_search_relaxations(request)
    assert len(items) == 3
    fingerprints = [repr(i.suggested_query) for i in items]
    assert len(fingerprints) == len(set(fingerprints))
