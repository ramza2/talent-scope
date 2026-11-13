"""Pure, source-independent tests for opt-in v30 row matching."""
from __future__ import annotations

import pytest

from app.modules.analysis.project_row_match_v30 import (
    build_clusters, build_llm_group_hints, month_range,
)


def row(doc, rid, *, name="예제 개발 사업", period="2020.2.1~4.30", customer="고객사",
        bbox=(1., 2., 100., 20.)):
    return {
        "document_id": doc, "row_id": rid, "page_no": 1,
        "source": "pdf-native-table", "column_mapping_verified": True,
        "bbox": bbox,
        "fields": {"project_name": name, "period": period,
                   "customer": customer, "role": "PM"},
    }


def test_cross_document_exact_identity_and_reordered_rows():
    values = [
        row("a", "1", name="예제 사업 1"),
        row("a", "2", name="예제 사업 2"),
        row("b", "3", name="예제사업2"),
        row("b", "4", name="예제사업1"),
    ]
    result = build_clusters(values)
    assert result["group_count"] == 2
    assert result["safe_multi_document_group_count"] == 2
    assert all(len(g["sources"]) == 2 for g in result["groups"])


def test_mismatched_project_names_never_auto_merge():
    result = build_clusters([row("a", "1"), row("b", "2", name="별도 사업")])
    assert result["group_count"] == 2
    assert result["safe_multi_document_group_count"] == 0
    assert len(result["review_suggestions"]) == 1


def test_mismatched_customer_or_dates_never_auto_merge():
    result = build_clusters([
        row("a", "1"), row("b", "2", customer="다른 기관"),
        row("c", "3", period="2020.3.1~4.30"),
    ])
    assert result["safe_multi_document_group_count"] == 0
    assert result["group_count"] == 3


def test_same_document_duplicate_rejected():
    result = build_clusters([row("a", "1"), row("a", "2")])
    assert result["safe_multi_document_group_count"] == 0
    assert result["status_counts"]["ambiguous_same_document_key"] == 2


def test_invalid_dates_or_missing_geometry_fail_closed():
    result = build_clusters([
        row("a", "1", period="2020.13.44~2020.15.44"),
        row("b", "2", bbox=None),
    ])
    assert result["status_counts"]["unverified_source_row"] == 2


@pytest.mark.parametrize(("raw", "expected"), [
    ("2017 .10 .1~ 20181130 . .", ("2017.10", "2018.11")),
    ("2016 .8 .1~ 2017730 . .", ("2016.08", "2017.07")),
    ("2015 .11 .1~ 2016130", ("2015.11", "2016.01")),
    ("2023 ..7 .3 ~ 현재", ("2023.07", "현재")),
])
def test_packed_pdf_dates(raw, expected):
    assert month_range(raw) == expected


def test_llm_hint_generator_strict_budget():
    result = build_clusters([row("a", "1"), row("b", "2")])
    text = build_llm_group_hints(result)
    assert "PROJECT_ROW_GROUP_HINTS_JSON" in text
    assert "quote_text" in text
    with pytest.raises(ValueError, match="PROJECT_ROW_INPUT_EXCEEDS_BUDGET"):
        build_llm_group_hints(result, max_chars=50)
