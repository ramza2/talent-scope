"""Pure Python unit tests; no DB, FastAPI or model gateway needed."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.profile_project_source_first_preview_noai import build_preview


def sample():
    clusters = {
        "mode": "readonly_v30_row_cluster_preview", "run_id": "r",
        "candidate_sha256_unchanged": "sha", "group_count": 3,
        "safe_multi_document_group_count": 1,
        "groups": [
            {"group_id": "g1", "status": "verified_exact_multi_document", "sources": []},
            {"group_id": "g2", "status": "single_source_unmatched", "sources": []},
            {"group_id": "g3", "status": "single_source_unmatched", "sources": []},
        ],
        "review_suggestions": [
            {"status": "needs_review_no_auto_merge", "group_ids": ["g2", "g3"]}
        ],
    }
    diagnosis = {
        "run_id": "r", "candidate_sha256_unchanged": "sha",
        "diagnostics": {
            "mode": "readonly_reason_diagnostics_no_ai_no_mutation",
            "total_candidate_count": 4,
            "reference_duplicate_extras": 1,
            "per_candidate": [
                {"index": 0, "reason": "fully_evidence_bound", "group_id": "g1"},
                {"index": 1, "reason": "literal_quote_lacks_project_title", "group_id": "g1"},
                {"index": 2, "reason": "fully_evidence_bound", "group_id": "g2"},
                {"index": 3, "reason": "title_customer_no_group", "group_id": None},
            ],
        },
    }
    rowcell = {
        "run_id": "r", "mode": "readonly_no_model_calls",
        "candidate_sha256_unchanged": "sha", "project_count": 4,
        "evaluations": {"raw": {"actual_count": 4, "duplicate_title_count": 1}},
    }
    return clusters, diagnosis, rowcell


class SourceFirstPreviewTests(unittest.TestCase):
    def test_counts_and_review_pair_preserved(self):
        c, d, r = sample()
        before = copy.deepcopy((c, d, r))
        result = build_preview(c, d, r)
        self.assertEqual((c, d, r), before)
        self.assertEqual(result["source_group_count"], 3)
        self.assertEqual(result["source_groups_with_candidate"], 2)
        self.assertEqual(result["source_groups_without_candidate"], 1)
        self.assertEqual(result["source_groups_with_multiple_candidates"], 1)
        self.assertEqual(result["duplicate_candidate_attachments_to_same_source_group"], 1)
        self.assertEqual(result["unmatched_candidate_count"], 1)
        self.assertEqual(result["review_only_pair_count"], 1)
        self.assertEqual(result["review_only_pairs"][0]["candidate_indices"], [[2], []])

    def test_no_private_titles_quotes_in_output(self):
        c, d, r = sample()
        c["groups"][0]["project_names"] = ["PRIVATE_TITLE"]
        c["groups"][0]["sources"] = [{"quote": "PRIVATE_QUOTE"}]
        self.assertNotIn("PRIVATE_TITLE", str(build_preview(c, d, r)))
        self.assertNotIn("PRIVATE_QUOTE", str(build_preview(c, d, r)))

    def test_reject_wrong_run(self):
        c, d, r = sample()
        d["run_id"] = "foreign"
        with self.assertRaisesRegex(ValueError, "SOURCE_OR_RUN_FINGERPRINT_MISMATCH"):
            build_preview(c, d, r)

    def test_reject_unknown_group(self):
        c, d, r = sample()
        d["diagnostics"]["per_candidate"][1]["group_id"] = "nope"
        with self.assertRaisesRegex(ValueError, "CANDIDATE_LINKS_TO_UNKNOWN_SOURCE_GROUP"):
            build_preview(c, d, r)

    def test_reject_incomplete_candidate_indices(self):
        c, d, r = sample()
        d["diagnostics"]["per_candidate"][3]["index"] = 1
        with self.assertRaisesRegex(ValueError, "CANDIDATE_INDEX_INCOMPLETE"):
            build_preview(c, d, r)

    def test_reject_invalid_review_pair(self):
        c, d, r = sample()
        c["review_suggestions"][0]["group_ids"] = ["g1", "g2"]
        with self.assertRaisesRegex(ValueError, "INVALID_REVIEW_ONLY_SOURCE_PAIR"):
            build_preview(c, d, r)

    def test_reject_duplicate_groups(self):
        c, d, r = sample()
        c["groups"][1]["group_id"] = "g1"
        with self.assertRaisesRegex(ValueError, "UNVERIFIED_OR_DUPLICATE_SOURCE_GROUPS"):
            build_preview(c, d, r)

    def test_reject_reference_metric_mismatch(self):
        c, d, r = sample()
        r["evaluations"]["raw"]["duplicate_title_count"] = 999
        with self.assertRaisesRegex(ValueError, "RAW_REFERENCE_METRIC_MISMATCH"):
            build_preview(c, d, r)


if __name__ == "__main__":
    unittest.main()
