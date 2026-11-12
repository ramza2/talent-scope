"""v30 row-specific quote matching tests. Synthetic evidence only."""
from __future__ import annotations

import unittest

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument, ProjectCandidate, SourceRef,
)
from app.modules.analysis.project_row_cell_dedupe_v30 import preview_row_cell_merge


TITLE = "클라우드데이터통합플랫폼구축사업"
PART = "데이터통합플랫폼"
PERIOD = "2020.02.01 ~ 2020.04.30"
PAGE = f"{PERIOD} {TITLE} 고객사 PL"


def source(row_id="a", title=TITLE, period=PERIOD, doc="doc1", page=1):
    return {
        "row_id": row_id, "document_id": doc, "page_no": page,
        "project_name": title, "customer": "고객사",
        "period_raw": period, "role": "PL",
    }


def group(gid="g1", sources=None, period=None):
    return {
        "group_id": gid, "status": "verified_exact_multi_document",
        "period_months": period or ["2020.02", "2020.04"],
        "sources": sources or [source()],
    }


def project(quote=TITLE, *, start="2020.02", end="2020.04", doc="doc1", page=1):
    return ProjectCandidate(
        project_name=TITLE, customer_name="고객사",
        start_date=start, end_date=end,
        source_refs=[SourceRef(document_id=doc, page_no=page, quote_text=quote)],
    )


class RowCellPreviewTests(unittest.TestCase):
    def run_preview(self, candidates, *, groups=None, text=PAGE):
        model = ProfileCandidateDocument(projects=candidates)
        original = model.model_dump(mode="json")
        result = preview_row_cell_merge(
            model,
            groups=groups if groups is not None else [group()],
            page_texts={("doc1", 1): text},
        )
        self.assertEqual(model.model_dump(mode="json"), original)
        self.assertNotIn(TITLE, str(result.stats))
        return result

    def test_unique_title_fragment_and_full_title_merge(self):
        result = self.run_preview([project(), project(PART)])
        self.assertEqual(result.stats["merged_extra_count"], 1)
        self.assertEqual(len(result.candidate.projects), 1)
        self.assertEqual(len(result.candidate.projects[0].source_refs), 2)

    def test_full_native_period_cell_can_anchor(self):
        result = self.run_preview([project(), project(PERIOD)])
        self.assertEqual(result.stats["merged_extra_count"], 1)

    def test_generic_role_quote_cannot_anchor(self):
        result = self.run_preview([project(), project("고객사 PL")])
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_short_date_token_cannot_anchor(self):
        result = self.run_preview(
            [project(), project("2020.02")],
            text=PAGE + " 2020.02",
        )
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_fragment_appearing_in_other_row_cannot_anchor(self):
        other = source(
            row_id="b",
            title="공공데이터통합플랫폼고도화사업",
            period="2019.01.01~2019.12.31",
        )
        result = self.run_preview(
            [project(), project(PART)],
            groups=[group(), group("g2", [other], ["2019.01", "2019.12"])],
        )
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_matching_title_and_page_without_literal_quote_fails(self):
        result = self.run_preview([project(), project("다른인용문")])
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_wrong_page_is_not_row_anchored(self):
        result = self.run_preview([project(), project(PART, page=2)])
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_month_level_equal_day_level_different_not_merged(self):
        result = self.run_preview([
            project(start="2020.02.01", end="2020.04.10"),
            project(PART, start="2020.02.15", end="2020.04.30"),
        ])
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_different_explicit_period_kept(self):
        result = self.run_preview([
            project(), project(PART, start="2020.03", end="2020.04")
        ])
        self.assertEqual(result.stats["merged_extra_count"], 0)

    def test_empty_sources_kept(self):
        result = self.run_preview([project(), project(PART)], groups=[])
        self.assertEqual(result.stats["merged_extra_count"], 0)
        self.assertEqual(result.stats["reject_counts"]["no_unique_title_customer_group"], 2)

    def test_solo_candidate_kept(self):
        result = self.run_preview([project()])
        self.assertEqual(result.stats["merged_extra_count"], 0)
        self.assertEqual(len(result.candidate.projects), 1)


if __name__ == "__main__":
    unittest.main()
