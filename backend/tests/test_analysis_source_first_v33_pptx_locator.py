"""V33: verified PPTX native locators persist on candidate/Diff (no DB)."""

from __future__ import annotations

import copy

import pytest

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument,
    ProjectCandidate,
)
from app.modules.analysis.confirm import _strip_metadata
from app.modules.analysis.diff_engine import DiffSpec
from app.modules.analysis.project_row_grouping import _row
from app.modules.analysis.project_source_first_native_direct_v30 import (
    assemble_native_direct,
    verified_pptx_source_locations,
)
from app.modules.analysis.schemas import CreateAnalysisRequest


COLUMNS = {"period": 0, "project_name": 1, "role": 2, "customer": 3}


def _pptx_row(
    doc_id: str,
    page_no: int,
    row_index: int,
    cells: list[str],
    *,
    table_index: int = 1,
    height: int = 508000,
    bbox=None,
):
    if bbox is None and height > 0:
        bbox = [10, 20, 300, 45]
    entry = _row(
        doc_id, page_no, "pptx-native-table", row_index, cells, COLUMNS, bbox,
    )
    entry["pptx_locator"] = {
        "slide_no": page_no,
        "table_index": table_index,
        "row_index": row_index,
        "period_column": 0,
        "project_name_column": 1,
        "customer_column": 3,
        "row_height_emu": height,
    }
    entry["geometry_space"] = (
        "pptx-ooxml-row" if height == 0 else "pptx-slide-points"
    )
    if height == 0:
        entry["bbox"] = None
    return entry


def _v33_fixture_rows():
    """32 multi-doc groups (2 locs each) + 2 single-doc groups → 66 locs."""
    rows = []
    for i in range(32):
        cells = [
            f"201{i % 9}.01~201{i % 9}.06",
            f"비공개다중프로젝트{i:02d}",
            "PL",
            "비공개고객기관",
        ]
        rows.append(_pptx_row(f"doc-a-{i}", 1, 1, cells, table_index=1))
        rows.append(_pptx_row(f"doc-b-{i}", 2, 1, cells, table_index=1))
    for i in range(2):
        cells = [
            f"2018.0{i + 1}~2018.0{i + 2}",
            f"비공개단일프로젝트{i:02d}",
            "PM",
            "비공개고객기관",
        ]
        rows.append(_pptx_row(f"doc-single-{i}", 1, i + 1, cells, table_index=1))
    return rows


def test_v33_preserves_66_pptx_locations_across_34_groups():
    rows = _v33_fixture_rows()
    assert len(rows) == 66
    result = assemble_native_direct(
        native_rows=rows,
        original_candidate_json={"profile": {"name": "시험사용자"}, "projects": []},
        latest_confirmed_snapshot={"projects": []},
        page_texts={},
    )
    assert result.diagnostics["native_project_groups"] == 34
    assert result.diagnostics["candidate_project_count"] == 34
    assert result.diagnostics["native_pptx_location_count"] == 66

    multi = [
        p for p in result.candidate.projects
        if len(p.native_source_locations) == 2
    ]
    single = [
        p for p in result.candidate.projects
        if len(p.native_source_locations) == 1
    ]
    assert len(multi) == 32
    assert len(single) == 2
    assert sum(len(p.native_source_locations) for p in result.candidate.projects) == 66

    storage = result.candidate.to_storage_dict()
    stored_locs = [
        loc
        for project in storage["projects"]
        for loc in project["native_source_locations"]
    ]
    assert len(stored_locs) == 66
    for loc in stored_locs:
        assert set(loc) == {
            "document_id", "page_no", "table_index", "row_index", "row_id",
        }
        assert loc["table_index"] >= 1
        assert loc["page_no"] >= 1
        assert loc["row_index"] >= 0
        assert loc["document_id"]
        assert loc["row_id"]

    # Multi-document groups keep both distinct document ids.
    for project in multi:
        docs = {loc.document_id for loc in project.native_source_locations}
        assert len(docs) == 2

    # Root Diff new_value carries the same locators (review UI / field fallback).
    root_specs = [
        s for s in result.specs
        if s.entity_type == "PROJECT" and s.field_name is None
    ]
    assert len(root_specs) == 34
    diff_loc_count = 0
    for spec in root_specs:
        assert isinstance(spec.new_value, dict)
        locs = spec.new_value.get("native_source_locations") or []
        assert locs
        diff_loc_count += len(locs)
    assert diff_loc_count == 66


def test_v33_rejects_invalid_pptx_locator_without_guessing():
    good = _pptx_row(
        "doc1", 1, 1,
        ["2020.02~2020.04", "비공개플랫폼구축", "PL", "비공개고객"],
    )
    with pytest.raises(ValueError, match="NATIVE_V30_NO_NATIVE_PROVENANCE"):
        verified_pptx_source_locations([])

    broken = copy.deepcopy(good)
    broken["pptx_locator"]["table_index"] = 0
    # Cluster rejects first; helper also fails closed if called directly.
    from app.modules.analysis.project_row_match_v30 import (
        verified_native_source_location,
    )
    assert verified_native_source_location(broken) is False
    with pytest.raises(ValueError, match="NATIVE_V30_INVALID_ROW_PROVENANCE"):
        verified_pptx_source_locations([broken])

    mismatched = copy.deepcopy(good)
    mismatched["pptx_locator"]["slide_no"] = 99
    assert verified_native_source_location(mismatched) is False
    with pytest.raises(ValueError, match="NATIVE_V30_INVALID_ROW_PROVENANCE"):
        verified_pptx_source_locations([mismatched])

    # Assembler fails closed when a source in a group is mutated after clustering
    # is simulated by feeding only invalid pptx rows.
    with pytest.raises(ValueError, match="NATIVE_V30_UNVERIFIED"):
        assemble_native_direct(
            native_rows=[broken],
            original_candidate_json={
                "profile": {"name": "시험사용자"}, "projects": [],
            },
            latest_confirmed_snapshot={"projects": []},
            page_texts={},
        )


def test_v33_non_optin_v25_format_omits_empty_locations():
    project = ProjectCandidate(
        project_name="일반프로젝트",
        customer_name="고객",
        start_date="2020.01",
        end_date="2020.06",
        source_refs=[{"document_id": "d1", "page_no": 1, "quote_text": "원문"}],
    )
    dumped = project.model_dump(mode="json")
    assert "native_source_locations" not in dumped

    doc = ProfileCandidateDocument.model_validate({
        "profile": {"name": "시험사용자"},
        "projects": [{
            "project_name": "일반프로젝트",
            "customer_name": "고객",
            "start_date": "2020.01",
            "end_date": "2020.06",
            "source_refs": [{
                "document_id": "d1", "page_no": 1, "quote_text": "원문",
            }],
        }],
    })
    storage = doc.to_storage_dict()
    assert "native_source_locations" not in storage["projects"][0]
    assert storage["projects"][0]["source_refs"][0]["quote_text"] == "원문"

    # Confirm still strips locator metadata (approve/confirm path unchanged).
    stripped = _strip_metadata({
        "project_name": "일반프로젝트",
        "native_source_locations": [{
            "document_id": "d1",
            "page_no": 1,
            "table_index": 1,
            "row_index": 1,
            "row_id": "abc",
        }],
        "source_refs": [{"document_id": "d1", "page_no": 1, "quote_text": "원문"}],
    })
    assert "native_source_locations" not in stripped
    assert "source_refs" not in stripped
    assert stripped["project_name"] == "일반프로젝트"

    # Default create request remains non-opt-in (no new flag required for V33).
    payload = CreateAnalysisRequest(
        person_id="00000000-0000-4000-8000-000000000001",
        document_ids=["00000000-0000-4000-8000-000000000002"],
    )
    assert payload.source_first_v30 is False
    assert payload.source_first_v31 is False
    assert payload.source_first_v32 is False


def test_v33_pdf_native_groups_keep_ui_without_pptx_locations():
    pdf = _row(
        "doc1", 1, "pdf-native-table", 1,
        ["2020.02~2020.04", "비공개PDF프로젝트", "PL", "비공개고객"],
        COLUMNS, [10, 20, 300, 45],
    )
    result = assemble_native_direct(
        native_rows=[pdf],
        original_candidate_json={"profile": {"name": "시험사용자"}, "projects": []},
        latest_confirmed_snapshot={"projects": []},
        page_texts={},
    )
    assert result.diagnostics["native_pptx_location_count"] == 0
    assert result.candidate.projects[0].native_source_locations == []
    assert "native_source_locations" not in result.candidate.to_storage_dict()["projects"][0]


def test_v33_root_and_field_diff_share_candidate_locations():
    rows = [
        _pptx_row(
            "doc1", 1, 1,
            ["2020.02~2020.04", "비공개공유프로젝트", "PL", "비공개고객"],
        ),
        _pptx_row(
            "doc2", 1, 1,
            ["2020.02~2020.04", "비공개공유프로젝트", "PL", "비공개고객"],
        ),
    ]
    result = assemble_native_direct(
        native_rows=rows,
        original_candidate_json={"profile": {"name": "시험사용자"}, "projects": []},
        latest_confirmed_snapshot={"projects": []},
        page_texts={},
    )
    project = result.candidate.projects[0]
    assert len(project.native_source_locations) == 2
    root = next(
        s for s in result.specs
        if s.candidate_path == "projects[0]" and s.field_name is None
    )
    assert len(root.new_value["native_source_locations"]) == 2

    # Field Diff simulates a responsibilities-only row; locations live on candidate.
    field = DiffSpec(
        entity_type="PROJECT",
        candidate_path="projects[0].responsibilities",
        field_name="responsibilities",
        change_type="NEW",
        new_value=project.responsibilities,
        source_refs=[],
    )
    candidate_json = result.candidate.to_storage_dict()
    parent = candidate_json["projects"][0]
    assert parent["native_source_locations"]
    assert field.new_value == "수행역할: PL"
    assert "native_source_locations" not in (
        field.new_value if isinstance(field.new_value, dict) else {}
    )


def test_v33_zero_height_pptx_locator_still_preserved():
    row = _pptx_row(
        "doc1", 1, 1,
        ["2020.02~2020.04", "비공개제로높이", "PL", "비공개고객"],
        height=0,
    )
    locs = verified_pptx_source_locations([row])
    assert len(locs) == 1
    assert locs[0].table_index == 1
    assert locs[0].row_index == 1
