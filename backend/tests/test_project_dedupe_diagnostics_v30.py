"""Diagnostic guards; synthetic titles/quotes only, no DB or LLM."""
from __future__ import annotations

import unittest

from app.ai.schemas.profile_candidate import ProfileCandidateDocument, ProjectCandidate, SourceRef
from app.modules.analysis.project_dedupe_diagnostics_v30 import diagnose


TITLE = "대민 플랫폼 개발"
PAGE = "2020.02~2020.04 대민 플랫폼 개발 고객사 PM"
SLOT = ("doc1", 1)


def project(*, title=TITLE, quote=TITLE, doc_id="doc1", page=1,
            start="2020.02", end="2020.04"):
    return ProjectCandidate(
        project_name=title, customer_name="고객사",
        start_date=start, end_date=end,
        source_refs=[
            SourceRef(document_id=doc_id, page_no=page, quote_text=quote)
        ],
    )


def groups():
    return [{
        "group_id": "group-1", "status": "verified_exact_multi_document",
        "period_months": ["2020.02", "2020.04"],
        "sources": [{
            "document_id": "doc1", "page_no": 1, "row_id": "row-1",
            "project_name": TITLE, "customer": "고객사",
        }],
    }]


REFERENCE = {
    "review_status": "provisional",
    "projects": [{
        "id": 17, "project_name": TITLE, "aliases": [],
        "customer_name": "고객사", "start_date": "2020.02", "end_date": "2020.04",
    }],
}


class DiagnosticsTests(unittest.TestCase):
    def do(self, candidates, *, page=PAGE, source_groups=None):
        original = ProfileCandidateDocument(projects=candidates)
        frozen = original.model_dump(mode="json")
        result = diagnose(
            original,
            groups=groups() if source_groups is None else source_groups,
            page_texts={SLOT: page},
            reference=REFERENCE,
        )
        self.assertEqual(original.model_dump(mode="json"), frozen)
        self.assertNotIn(TITLE, str(result))
        self.assertNotIn(PAGE, str(result))
        return result

    def test_evidence_bound(self):
        self.assertEqual(self.do([project()])["reason_counts"], {"fully_evidence_bound": 1})

    def test_valid_page_quote_without_title_is_not_sufficient(self):
        self.assertEqual(
            self.do([project(quote="고객사 PM")])["reason_counts"],
            {"literal_quote_lacks_project_title": 1},
        )

    def test_nonliteral_quote(self):
        self.assertEqual(
            self.do([project(quote="대민 플랫폼 12차 개발")])["reason_counts"],
            {"quote_not_literal_on_stored_page": 1},
        )

    def test_wrong_page(self):
        self.assertEqual(self.do([project(page=2)])["reason_counts"],
                         {"ref_page_not_in_group": 1})

    def test_period_conflict(self):
        self.assertEqual(self.do([project(start="2020.03")])["reason_counts"],
                         {"source_period_conflict": 1})

    def test_no_group_for_different_title(self):
        self.assertEqual(
            self.do([project(title="별도 구축 사업")])["reason_counts"],
            {"title_customer_no_group": 1},
        )

    def test_reference_duplicate_indices_only(self):
        r = self.do([project(), project(start="2020.03")])
        self.assertEqual(r["reference_duplicate_extras"], 1)
        self.assertEqual(r["reference_duplicate_groups"][0]["reference_id"], 17)
        self.assertEqual(r["reference_duplicate_groups"][0]["candidate_indices"], [0, 1])
        self.assertEqual(r["reason_counts"]["source_period_conflict"], 1)

    def test_ambiguous_two_groups_fail_closed(self):
        gs = groups()
        gs.append(dict(gs[0], group_id="other-id"))
        self.assertEqual(
            self.do([project()], source_groups=gs)["reason_counts"],
            {"title_customer_ambiguous_group": 1},
        )


if __name__ == "__main__":
    unittest.main()
