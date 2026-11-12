"""Tests for source-first row-list preview, privacy and candidate preservation."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.profile_project_source_first_rows_noai import build_rows


def source(rid, doc, title, *, role="PL"):
    return {
        "row_id": rid, "document_id": doc, "page_no": 1,
        "bbox": [10, 20, 30, 40],
        "project_name": title, "customer": "비공개고객기관",
        "role": role, "period_raw": "2020.02.01~2020.04.30",
    }


def inputs():
    clusters = {
        "run_id": "run",
        "mode": "readonly_v30_row_cluster_preview",
        "group_count": 3,
        "safe_multi_document_group_count": 1,
        "groups": [
            {
                "group_id": "g1", "status": "verified_exact_multi_document",
                "period_months": ["2020.02", "2020.04"],
                "sources": [
                    source("a", "doc1", "비공개공공플랫폼개발"),
                    source("b", "doc2", "비공개공공플랫폼개발"),
                ],
            },
            {
                "group_id": "g2", "status": "single_source_unmatched",
                "period_months": ["2020.02", "2020.04"],
                "sources": [source("c", "doc1", "비공개다른사업")],
            },
            {
                "group_id": "g3", "status": "single_source_unmatched",
                "period_months": ["2020.02", "2020.04"],
                "sources": [source("d", "doc2", "비공개명칭차이사업")],
            },
        ],
    }
    preview = {
        "mode": "source_first_identity_preview_no_ai_no_db",
        "run_id": "run", "read_only": True, "no_candidate_text_in_output": True,
        "source_group_count": 3,
        "candidate_count": 4,
        "candidates_attached_by_title_customer": 3,
        "unmatched_candidate_count": 1,
        "source_groups_with_multiple_candidates": 1,
        "source_groups_without_candidate": 1,
        "review_only_pair_count": 1,
        "review_only_pairs": [
            {"group_ids": ["g2", "g3"], "candidate_indices": [[2], []],
             "decision": "manual_review_only_no_auto_merge"},
        ],
        "candidate_unmatched": [{"index": 3, "reason": "title_customer_no_group"}],
        "group_previews": [
            {
                "group_id": "g1", "status": "verified_exact_multi_document",
                "candidate_indices": [0, 1],
                "candidate_evidence_reasons": [
                    "fully_evidence_bound", "literal_quote_lacks_project_title",
                ],
            },
            {
                "group_id": "g2", "status": "single_source_unmatched",
                "candidate_indices": [2],
                "candidate_evidence_reasons": ["fully_evidence_bound"],
            },
            {
                "group_id": "g3", "status": "single_source_unmatched",
                "candidate_indices": [], "candidate_evidence_reasons": [],
            },
        ],
    }
    return clusters, preview


class SourceFirstRowTests(unittest.TestCase):
    def test_preserve_source_groups_and_review_pairs(self):
        c, p = inputs()
        frozen = copy.deepcopy((c, p))
        public, private = build_rows(c, p)
        self.assertEqual((c, p), frozen)
        self.assertEqual(public["source_group_count"], 3)
        self.assertEqual(public["source_row_without_candidate_count"], 1)
        self.assertEqual(public["multi_candidate_review_group_count"], 1)
        self.assertEqual(public["one_verified_candidate_group_count"], 1)
        self.assertEqual(public["manual_name_variant_review_pair_count"], 1)
        self.assertEqual(len(private["source_groups"]), 3)
        self.assertTrue(all(not x["safe_to_auto_confirm"] for x in private["source_groups"]))
        self.assertEqual(private["unmatched_candidate_indices"], [3])

    def test_private_rows_preserve_source_provenance(self):
        c, p = inputs()
        _public, private = build_rows(c, p)
        rows = {r["group_id"]: r for r in private["source_groups"]}
        self.assertEqual(rows["g1"]["display_name_from_source"], "비공개공공플랫폼개발")
        self.assertEqual(len(rows["g1"]["source_row_evidence"]), 2)
        self.assertEqual(rows["g1"]["source_row_evidence"][0]["bbox"], [10, 20, 30, 40])
        self.assertEqual(rows["g3"]["candidate_link_status"], "source_row_without_candidate")
        self.assertTrue(rows["g3"]["review_name_variant_pair"])

    def test_public_report_redacts_titles_customer_and_roles(self):
        c, p = inputs()
        public, _private = build_rows(c, p)
        s = str(public)
        for forbidden in ("비공개", "공공플랫폼", "2020.02.01", "PL"):
            self.assertNotIn(forbidden, s)
        self.assertNotIn("source_row_evidence", s)

    def test_reject_foreign_run(self):
        c, p = inputs()
        p["run_id"] = "foreign"
        with self.assertRaisesRegex(ValueError, "INVALID_SOURCE_FIRST_INPUT"):
            build_rows(c, p)

    def test_reject_duplicated_candidate_link(self):
        c, p = inputs()
        p["group_previews"][1]["candidate_indices"] = [0]
        with self.assertRaisesRegex(ValueError, "CANDIDATE_INDEX_LINKAGE_INVALID"):
            build_rows(c, p)

    def test_reject_missing_candidate_coverage(self):
        c, p = inputs()
        p["candidate_unmatched"] = []
        with self.assertRaisesRegex(ValueError, "SOURCE_FIRST_SUMMARY_MISMATCH"):
            build_rows(c, p)

    def test_reject_invalid_bbox(self):
        c, p = inputs()
        c["groups"][0]["sources"][0]["bbox"] = None
        with self.assertRaisesRegex(ValueError, "SOURCE_ROW_PROVENANCE_INVALID"):
            build_rows(c, p)

    def test_reject_false_review_pair(self):
        c, p = inputs()
        p["review_only_pairs"][0]["group_ids"] = ["g1", "g3"]
        with self.assertRaisesRegex(ValueError, "INVALID_MANUAL_REVIEW_PAIR"):
            build_rows(c, p)

    def test_reject_multi_doc_duplicate_source_ids(self):
        c, p = inputs()
        c["groups"][0]["sources"][1]["document_id"] = "doc1"
        with self.assertRaisesRegex(ValueError, "SOURCE_MULTI_DOCUMENT_IDENTITY_INVALID"):
            build_rows(c, p)


if __name__ == "__main__":
    unittest.main()
