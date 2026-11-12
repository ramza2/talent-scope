"""Read-only v30 candidate row postprocessing tests.

All data is synthetic; no DB, network, or model calls.
"""
from __future__ import annotations

import unittest

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument, ProjectCandidate, SourceRef, CodeRefCandidate
)
from app.modules.analysis.project_candidate_row_dedupe_v30 import (
    preview_candidate_row_dedupe
)


def src(doc="doc1", page=1, title="환경 데이터 통합사업"):
    return {"document_id": doc, "page_no": page, "row_id": doc + "row",
            "project_name": title, "customer": "환경기관"}


def group(gid="groupA", period=None, sources=None):
    return {"group_id": gid, "status": "verified_exact_multi_document",
            "period_months": period or ["2020.02", "2020.04"],
            "sources": sources or [src()]}


def candidate(start="2020.02", end="2020.04", *, title="환경 데이터 통합사업",
              customer="환경기관", doc="doc1", page=1,
              quote=None, jobs=None):
    return ProjectCandidate(
        project_name=title, customer_name=customer,
        start_date=start, end_date=end,
        jobs=jobs or [],
        source_refs=[SourceRef(document_id=doc, page_no=page,
                               quote_text=quote if quote is not None else title)]
    )


class DedupPreviewTests(unittest.TestCase):
    def setUp(self):
        self.group = group()
        self.pages = {("doc1", 1): "2020.2.1~4.30 환경 데이터 통합사업 환경기관 PL"}
        self.good = candidate()

    def preview(self, projects, groups=None, pages=None):
        doc = ProfileCandidateDocument(projects=projects)
        original = doc.model_dump(mode="json")
        outcome = preview_candidate_row_dedupe(
            doc, groups=groups if groups is not None else [self.group],
            page_texts=pages if pages is not None else self.pages,
        )
        self.assertEqual(doc.model_dump(mode="json"), original)
        return outcome

    def test_identical_strong_candidates_merge_and_union_evidence(self):
        a = self.good
        b = candidate(quote="환경 데이터 통합사업 환경기관 PL",
                      jobs=[CodeRefCandidate(code="JOB-MGT-PL")])
        outcome = self.preview([a, b])
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 1)
        self.assertEqual(len(outcome.strict.projects), 1)
        self.assertEqual(len(outcome.strict.projects[0].source_refs), 2)
        self.assertEqual(outcome.strict.projects[0].jobs[0].code, "JOB-MGT-PL")
        self.assertEqual(len(outcome.quarantine_simulation.projects), 1)

    def test_same_month_but_different_days_do_not_merge(self):
        a = candidate(start="2020.02.01", end="2020.04.10")
        b = candidate(start="2020.02.15", end="2020.04.30")
        outcome = self.preview([a, b])
        self.assertEqual(len(outcome.strict.projects), 2)
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 0)
        self.assertEqual(outcome.diagnostics["date_conflict_review_count"], 0)

    def test_date_conflict_is_review_only_never_strict_delete(self):
        wrong = candidate(start="2019.09", end="2019.10")
        outcome = self.preview([self.good, wrong])
        self.assertEqual(len(outcome.strict.projects), 2)
        self.assertEqual(len(outcome.quarantine_simulation.projects), 1)
        self.assertEqual(outcome.diagnostics["date_conflict_review_count"], 1)
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 0)

    def test_wrong_date_without_correct_sibling_must_stay(self):
        wrong = candidate(start="2019.09", end="2019.10")
        outcome = self.preview([wrong])
        self.assertEqual(len(outcome.strict.projects), 1)
        self.assertEqual(len(outcome.quarantine_simulation.projects), 1)
        self.assertEqual(outcome.diagnostics["date_conflict_review_count"], 0)

    def test_ref_missing_original_quote_never_merge(self):
        a = candidate(quote="이 문구는 페이지에 없음")
        outcome = self.preview([self.good, a])
        self.assertEqual(len(outcome.strict.projects), 2)
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 0)

    def test_ref_page_not_owned_by_group_never_merge(self):
        p = candidate(doc="doc1", page=2)
        pages = dict(self.pages)
        pages[("doc1", 2)] = "환경 데이터 통합사업"
        outcome = self.preview([self.good, p], pages=pages)
        self.assertEqual(len(outcome.strict.projects), 2)

    def test_generic_quote_without_title_never_merge(self):
        generic = candidate(quote="환경기관 PL")
        outcome = self.preview([self.good, generic])
        self.assertEqual(len(outcome.strict.projects), 2)

    def test_two_different_source_periods_with_same_title_never_merge(self):
        other = group(gid="groupB", period=["2021.02", "2021.04"])
        self.assertEqual(len(self.preview(
            [self.good, candidate(start="2021.02", end="2021.04")],
            groups=[self.group, other],
        ).strict.projects), 2)

    def test_distinct_same_period_but_different_title_remain_separate(self):
        other = candidate(title="환경 데이터 고도화 사업")
        outcome = self.preview([self.good, other])
        self.assertEqual(len(outcome.strict.projects), 2)

    def test_merged_source_evidence_from_two_documents(self):
        g = group(sources=[src(), src(doc="doc2")])
        pages = dict(self.pages)
        pages[("doc2", 1)] = "2020.2.1~4.30 환경 데이터 통합사업 환경기관"
        a = self.good
        b = candidate(doc="doc2")
        outcome = self.preview([a, b], groups=[g], pages=pages)
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 1)
        self.assertEqual(len(outcome.strict.projects[0].source_refs), 2)

    def test_no_duplicate_means_no_change(self):
        outcome = self.preview([self.good])
        self.assertEqual(outcome.diagnostics["strict_removed_extra_count"], 0)
        self.assertEqual(outcome.diagnostics["date_conflict_review_count"], 0)
        self.assertEqual(len(outcome.strict.projects), 1)


if __name__ == "__main__":
    unittest.main()
