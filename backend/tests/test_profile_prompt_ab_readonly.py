"""Pure metric checks for the read-only v25/v26 comparison pilot."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.modules.analysis.service import AnalysisService
from scripts.profile_prompt_ab_readonly import compare_projects, project_metrics


def _candidate(*rows):
    return SimpleNamespace(projects=[
        SimpleNamespace(
            project_name=name,
            customer_name=customer,
            start_date=start,
            end_date=end,
            source_refs=refs,
        )
        for name, customer, start, end, refs in rows
    ])


def test_ab_metrics_report_role_only_titles_without_dropping_rows() -> None:
    candidate = _candidate(
        ("PM", "고객", "2021.06", "2021.09", ["근거"]),
        ("서울시 데이터 활용 정보전략계획", "서울시", "2022.02", "2022.05", []),
        ("PM", "고객", "2021.06", "2021.09", ["근거"]),
        (None, "고객", "2020.01", "2020.02", []),
    )
    assert project_metrics(candidate) == {
        "project_count": 4,
        "role_only_project_count": 2,
        "role_only_labels": ["PM"],
        "unnamed_project_count": 1,
        "duplicate_identity_count": 1,
        "project_evidence_count": 2,
    }


def test_ab_compare_uses_name_customer_and_period() -> None:
    baseline = _candidate(("PM", "고객", "2021.06", "2021.09", ["r"]))
    experiment = _candidate(("실제 프로젝트", "고객", "2021.06", "2021.09", ["r"]))
    summary = compare_projects(baseline, experiment)
    assert summary == {
        "v25_only_count": 1,
        "v26_only_count": 1,
        "common_identity_count": 0,
    }
    assert "v25_only_identities" not in summary
    assert "v26_only_identities" not in summary
    detail = compare_projects(baseline, experiment, include_names=True)
    assert detail["v25_only_identities"][0][0] == "pm"
    assert detail["v26_only_identities"][0][0] == "실제프로젝트"


def test_project_only_extraction_is_opt_in() -> None:
    method = AnalysisService._extract_staged_compact_candidate
    option = inspect.signature(method).parameters["projects_only"]
    assert option.default is False
