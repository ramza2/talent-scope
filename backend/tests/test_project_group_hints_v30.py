"""Pure page-scoped hint tests (unittest runnable in existing API container)."""
import unittest

from app.modules.analysis.project_group_hints_v30 import (
    add_group_hints, prompt_source_slots, scoped_group_data, validate_cluster_report,
)


def group(doc, rid, page, name):
    return {
        "group_id": rid,
        "status": "single_source_unmatched",
        "period_months": ["2020.01", "2020.12"],
        "sources": [{
            "document_id": doc,
            "page_no": page,
            "row_id": rid,
            "project_name": name,
            "customer": "기관",
            "role": "PM",
        }],
    }


class GroupHintTests(unittest.TestCase):
    def setUp(self):
        self.mapping = {"D1": "doc1", "D2": "doc2"}
        self.groups = [
            group("doc1", "a", 1, "사업 A"),
            group("doc1", "b", 2, "사업 B"),
            group("doc2", "c", 1, "사업 C"),
        ]

    def test_scopes_to_alias_and_page(self):
        blocks = "[DOCUMENT D1]\nfilename: f\ncontent:\n[PAGE 2]\n..."
        self.assertEqual(prompt_source_slots(blocks, self.mapping), {("doc1", 2)})
        selected = scoped_group_data(
            self.groups, document_blocks=blocks, alias_to_id=self.mapping
        )
        self.assertEqual([item["id"] for item in selected], ["b"])

    def test_hints_are_separate_and_original_prompt_unchanged(self):
        blocks = "[DOCUMENT D2]\nfilename: f\ncontent:\n[PAGE 1]\n..."
        original = "===== BEGIN UNTRUSTED DOCUMENT DATA =====\nhello"
        augmented = add_group_hints(
            original, document_blocks=blocks,
            groups=self.groups, alias_to_id=self.mapping,
        )
        self.assertTrue(augmented.startswith(original))
        self.assertIn("사업 C", augmented)
        self.assertNotIn("사업 A", augmented)
        self.assertIn("source_refs", augmented)

    def test_no_page_marker_means_no_hints(self):
        original = "evidence"
        self.assertEqual(add_group_hints(
            original, document_blocks="[DOCUMENT D1]\ntext",
            groups=self.groups, alias_to_id=self.mapping,
        ), original)

    def test_over_budget_fails_before_mutating_original(self):
        with self.assertRaisesRegex(ValueError, "SCOPED_GROUP_HINTS_EXCEED_BUDGET"):
            add_group_hints(
                "original",
                document_blocks="[DOCUMENT D1]\n[PAGE 1]\n",
                groups=self.groups, alias_to_id=self.mapping,
                max_hint_chars=5,
            )

    def test_report_rejects_foreign_document(self):
        report = {
            "run_id": "run",
            "candidate_sha256_unchanged": "sha",
            "mode": "readonly_v30_row_cluster_preview",
            "group_count": 1,
            "safe_multi_document_group_count": 0,
            "groups": [group("foreign", "a", 1, "사업 A")],
        }
        with self.assertRaisesRegex(ValueError, "GROUP_HINTS_UNKNOWN_DOCUMENT"):
            validate_cluster_report(
                report, run_id="run", candidate_sha="sha", known_documents={"doc1"}
            )

    def test_report_accepts_consistent_metadata(self):
        report = {
            "run_id": "run",
            "candidate_sha256_unchanged": "sha",
            "mode": "readonly_v30_row_cluster_preview",
            "group_count": 1,
            "safe_multi_document_group_count": 0,
            "groups": [group("doc1", "a", 1, "사업 A")],
        }
        self.assertEqual(len(validate_cluster_report(
            report, run_id="run", candidate_sha="sha", known_documents={"doc1"}
        )), 1)


if __name__ == "__main__":
    unittest.main()
