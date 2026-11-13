"""Opt-in Source-First runtime boundary tests (no DB or real AI)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ai.prompts.profile_extract import (
    SOURCE_FIRST_V30_PROMPT_VERSION,
    current_profile_prompt,
    resolve_profile_prompt,
)
from app.core.config import Settings
from app.modules.analysis.source_first_v30_runtime import assemble_live_source_first


class FakePDF:
    def __init__(self, length=2):
        self.length = length
        self.closed = False

    def __len__(self):
        return self.length

    def load_page(self, index):
        return SimpleNamespace(index=index)

    def close(self):
        self.closed = True


class FakeBuilder:
    def __init__(self, pdf):
        self.pdf = pdf

    def _open_render_source(self, doc):
        return self.pdf


def document(pages=2):
    return SimpleNamespace(
        id="doc-1",
        pages=[SimpleNamespace(page_no=i + 1) for i in range(pages)],
    )


def test_v30_is_opt_in_and_reuses_v25_prompts():
    assert Settings.model_fields["analysis_source_first_v30_enabled"].default is False
    from app.modules.analysis.service import AnalysisService

    service = AnalysisService.__new__(AnalysisService)
    service.settings = SimpleNamespace(analysis_source_first_v30_enabled=True)
    assert service._selected_profile_prompt().prompt_version == SOURCE_FIRST_V30_PROMPT_VERSION
    service.settings = SimpleNamespace(analysis_source_first_v30_enabled=False)
    assert service._selected_profile_prompt().prompt_version == "profile-extract-v25"
    v25 = current_profile_prompt()
    v30 = resolve_profile_prompt(SOURCE_FIRST_V30_PROMPT_VERSION)
    assert v25.prompt_version == "profile-extract-v25"
    assert v30.prompt_version != v25.prompt_version
    assert v30.core_system_prompt == v25.core_system_prompt
    assert v30.projects_system_prompt == v25.projects_system_prompt
    assert v30.build_projects_user_prompt is v25.build_projects_user_prompt


def test_live_native_scans_all_pages_and_preserves_v25_core():
    pdf = FakePDF()
    original = SimpleNamespace(to_storage_dict=lambda: {"core": "unchanged"})
    native_rows = [{"source": "pdf-native-table"}]
    expected = object()
    with patch(
        "app.modules.analysis.source_first_v30_runtime.native_table_groups",
        side_effect=[native_rows, native_rows],
    ) as scanner, patch(
        "app.modules.analysis.source_first_v30_runtime.assemble_native_direct",
        return_value=expected,
    ) as assemble:
        result = assemble_live_source_first(
            source_builder=FakeBuilder(pdf),
            documents=[document()],
            original_candidate=original,
            confirmed_snapshot={"projects": []},
            page_texts={},
        )
    assert result is expected
    assert scanner.call_count == 2
    assert assemble.call_args.kwargs["original_candidate_json"] == {"core": "unchanged"}
    assert len(assemble.call_args.kwargs["native_rows"]) == 2
    assert pdf.closed


def test_unavailable_pdf_falls_back():
    with pytest.raises(ValueError, match="PDF_UNAVAILABLE"):
        assemble_live_source_first(
            source_builder=FakeBuilder(None),
            documents=[document()],
            original_candidate=object(),
            confirmed_snapshot={"projects": []},
            page_texts={},
        )


def test_no_native_table_falls_back_and_closes_pdf():
    pdf = FakePDF()
    with patch(
        "app.modules.analysis.source_first_v30_runtime.native_table_groups",
        return_value=[],
    ):
        with pytest.raises(ValueError, match="WITHOUT_NATIVE_ROWS"):
            assemble_live_source_first(
                source_builder=FakeBuilder(pdf),
                documents=[document()],
                original_candidate=object(),
                confirmed_snapshot={"projects": []},
                page_texts={},
            )
    assert pdf.closed


def test_page_mismatch_falls_back_and_closes_pdf():
    pdf = FakePDF(length=1)
    with pytest.raises(ValueError, match="PAGE_MISMATCH"):
        assemble_live_source_first(
            source_builder=FakeBuilder(pdf),
            documents=[document()],
            original_candidate=object(),
            confirmed_snapshot={"projects": []},
            page_texts={},
        )
    assert pdf.closed


def test_runtime_requests_strict_rows_not_partial_source():
    pdf = FakePDF(length=1)
    original = SimpleNamespace(to_storage_dict=lambda: {})
    with patch(
        "app.modules.analysis.source_first_v30_runtime.native_table_groups",
        return_value=[{"source": "pdf-native-table"}],
    ) as scanner, patch(
        "app.modules.analysis.source_first_v30_runtime.assemble_native_direct",
        return_value=object(),
    ):
        assemble_live_source_first(
            source_builder=FakeBuilder(pdf),
            documents=[document(pages=1)],
            original_candidate=original,
            confirmed_snapshot={"projects": []},
            page_texts={},
        )
    assert scanner.call_args.kwargs["strict_rows"] is True
    assert pdf.closed
