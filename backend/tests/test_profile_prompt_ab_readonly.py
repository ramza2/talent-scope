"""Pure metric checks for the read-only v25/v26 comparison pilot."""

from __future__ import annotations

import argparse
import inspect
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.analysis.service import AnalysisService
from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)
from scripts import profile_prompt_ab_readonly as ab
from scripts.profile_prompt_ab_readonly import compare_projects, project_metrics
from scripts.profile_project_reference import (
    REFERENCE_SCHEMA,
    evaluate_projects,
    load_reference,
)


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


def test_ab_script_keeps_v25_v26_compare_and_adds_v27_v28_solo_options() -> None:
    assert ab.COMPARE_VERSIONS == ("profile-extract-v25", "profile-extract-v26")
    assert ab.VERSIONS == ab.COMPARE_VERSIONS
    assert ab.SOLO_VERSIONS == (
        "profile-extract-v27",
        "profile-extract-v28",
        "profile-extract-v29",
    )

    # Reuse the live CLI's solo flag contract without invoking LLM/DB.
    source = inspect.getsource(ab.main)
    assert "--solo-version" in source
    assert "compare_v25_v26" in source
    assert 'f"solo_{versions[0]}"' in source

    # Argparse choices match the exported solo tuple.
    action = argparse.ArgumentParser()
    action.add_argument("--solo-version", choices=ab.SOLO_VERSIONS)
    action.add_argument("--run-id", required=True)
    for version in ab.SOLO_VERSIONS:
        parsed = action.parse_args(
            ["--run-id", str(uuid4()), "--solo-version", version]
        )
        assert parsed.solo_version == version
    with pytest.raises(SystemExit):
        action.parse_args(
            ["--run-id", str(uuid4()), "--solo-version", "profile-extract-v26"]
        )


def test_reference_accepts_aliases_with_equivalent_punctuation(tmp_path: Path) -> None:
    payload = {
        "schema_version": REFERENCE_SCHEMA,
        "review_status": "provisional",
        "projects": [{
            "id": 1, "project_name": "프로젝트 A (1단계)",
            "aliases": ["프로젝트 A 1단계"],
            "customer_name": "기관", "start_date": "2020.01", "end_date": "2020.02",
        }],
    }
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_reference(path)["projects"][0]["id"] == 1


def test_reference_can_be_streamed_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "schema_version": REFERENCE_SCHEMA,
        "review_status": "provisional",
        "projects": [{
            "id": 1, "project_name": "프로젝트 A", "customer_name": "기관",
            "start_date": "2020.01", "end_date": "2020.02",
        }],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert load_reference(Path("-")) == payload


def test_reference_scorer_counts_duplicate_title_and_cross_row_date() -> None:
    reference = {
        "review_status": "provisional",
        "projects": [
            {
                "project_name": "ECM 구축", "aliases": ["문서중앙화 시스템 ECM 구축"],
                "customer_name": "SKT", "start_date": "2008.10", "end_date": "2010.12",
            },
            {
                "project_name": "광주은행 마이데이터",
                "customer_name": "광주은행", "start_date": "2021.06", "end_date": "2021.09",
            },
            {
                "project_name": "KBIZ 공제", "customer_name": "중소기업중앙회",
                "start_date": "2021.02", "end_date": "2021.10",
            },
        ],
    }
    candidate = _candidate(
        ("문서중앙화 시스템(ECM) 구축", "SKT", "2008.10", "2010.1", []),
        ("ECM 구축", "SKT", "2008.10", "2010.12", []),
        ("광주은행 마이데이터", "중소기업중앙회", "2021.02.01", "2021.10.30", []),
        ("없는 사업", "KT", "2020.01", "2020.02", []),
    )
    result = evaluate_projects(candidate, reference, include_names=True)
    assert result["reference_count"] == 3
    assert result["actual_count"] == 4
    assert result["matched_title_count"] == 2
    assert result["fully_correct_project_count"] == 1
    assert result["matched_customer_count"] == 1
    assert result["matched_start_month_count"] == 1
    assert result["matched_end_month_count"] == 1
    assert result["missing_title_count"] == 1
    assert result["unexpected_title_count"] == 1
    assert result["duplicate_title_count"] == 1
    assert result["project_precision_pct"] == 25.0
    assert result["project_recall_pct"] == 33.33
    assert result["duplicate_titles"][0]["extracted_count"] == 2
    assert result["field_issues"][0]["wrong_fields"] == [
        "customer", "start_month", "end_month",
    ]


def test_reference_scorer_normalizes_date_precision_and_customer_alias() -> None:
    reference = {
        "review_status": "verified",
        "projects": [{
            "project_name": "프로젝트 A", "customer_name": "한국양성평등교육원",
            "customer_aliases": ["한국양성평등교육진흥원"],
            "start_date": "2020.11", "end_date": "2020.12",
        }],
    }
    candidate = _candidate((
        "프로젝트 A", "한국양성평등교육진흥원", "2020.11.01", "2020.12.28", [],
    ))
    result = evaluate_projects(candidate, reference)
    assert result["fully_correct_project_count"] == 1
    assert result["project_precision_pct"] == 100.0
    assert result["project_recall_pct"] == 100.0
    assert "field_issues" not in result


def test_reference_rejects_cross_project_alias_collision(tmp_path: Path) -> None:
    reference = {
        "schema_version": REFERENCE_SCHEMA,
        "review_status": "provisional",
        "projects": [
            {
                "id": i, "project_name": name, "aliases": [alias],
                "customer_name": "기관", "start_date": "2020.01", "end_date": "2020.02",
            }
            for i, name, alias in [(1, "사업 A", "공통명"), (2, "사업 B", "공통명")]
        ],
    }
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(reference), encoding="utf-8")
    with pytest.raises(ValueError, match="DUPLICATE_OR_EMPTY_TITLE_ALIAS"):
        load_reference(path)


def test_v29_diagnostics_is_opt_in_and_rejects_incomplete_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", [
        "profile_prompt_ab_readonly.py",
        "--run-id", str(uuid4()),
        "--solo-version", "profile-extract-v29",
        "--diagnose-projects",
    ])
    with pytest.raises(SystemExit) as raised:
        ab.main()
    assert raised.value.code == 2


def test_v29_diagnostics_captures_duplicate_and_date_owner_evidence_without_quotes() -> None:
    page = (
        "2020.5.30~11.30\n"
        "사업 A\n기관\n"
        "2019.1.1~4.30\n"
        "사업 B\n고객\n"
    )
    ref_a = SourceRef(document_id="doc1", page_no=1, quote_text="사업 A")
    ref_b = SourceRef(document_id="doc1", page_no=1, quote_text="사업 B")
    candidate = ProfileCandidateDocument(projects=[
        ProjectCandidate(
            project_name="사업 A", customer_name="기관",
            start_date="2020.05", end_date="2020.11", source_refs=[ref_a],
        ),
        ProjectCandidate(
            project_name="사업 A", customer_name="기관",
            start_date="2019.01", end_date="2019.04", source_refs=[ref_a],
        ),
        ProjectCandidate(
            project_name="사업 B", customer_name="고객",
            start_date="2019.01", end_date="2019.04", source_refs=[ref_b],
        ),
        ProjectCandidate(
            project_name="미확인", customer_name="고객",
            start_date="2019.01", end_date="2019.04", source_refs=[],
        ),
    ])
    reference = {"review_status": "provisional", "projects": [
        {"id": 1, "project_name": "사업 A", "customer_name": "기관",
         "start_date": "2020.05", "end_date": "2020.11"},
        {"id": 2, "project_name": "사업 B", "customer_name": "고객",
         "start_date": "2019.01", "end_date": "2019.05"},
        {"id": 3, "project_name": "누락", "customer_name": "기관",
         "start_date": "2018.01", "end_date": "2018.02"},
    ]}
    original = candidate.model_dump()
    summary = ab.diagnose_projects(
        candidate,
        reference,
        page_texts={("doc1", 1): page},
        allowed_documents={"doc1": {1}},
    )
    scored = evaluate_projects(candidate, reference)

    assert summary["duplicate_title_extra_count"] == scored["duplicate_title_count"] == 1
    assert summary["field_issue_group_count"] == 1
    assert summary["diagnostic_group_count"] == 2
    assert summary["missing_reference_titles"] == [
        {"reference_id": 3, "project_name": "누락"},
    ]
    assert len(summary["unexpected_candidates"]) == 1
    a_group = summary["groups"][0]
    assert a_group["best_candidate_index_for_scoring"] == 0
    assert a_group["candidate_count"] == 2
    assert a_group["candidates"][0]["field_matches"] == {
        "customer": True, "start_month": True, "end_month": True,
    }
    assert a_group["candidates"][0]["shared_title_customer_ref_page_other_count"] == 1
    evidence = a_group["candidates"][0]["source_refs"][0]
    assert evidence["quote_exact_on_page"] is True
    assert evidence["page_allowed"] is True
    assert evidence["nearby_page_periods_unverified"][0]["start_date"] == "2020.05"
    assert len(evidence["quote_sha256_16"]) == 16
    assert "2020.5.30~11.30" not in json.dumps(summary, ensure_ascii=False)
    assert '"quote_text"' not in json.dumps(summary, ensure_ascii=False)
    assert candidate.model_dump() == original
