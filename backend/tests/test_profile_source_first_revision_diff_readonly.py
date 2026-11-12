"""No-DB unit tests for live-revision Diff comparison and fail-closed guards."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_project_source_first_bridge_v30 import input_pair
from app.modules.analysis.project_source_first_bridge_v30 import make_candidate_bridge
from scripts.profile_source_first_revision_diff_readonly import compare_revisions, _fingerprint


def bridge_candidate():
    preview, ai = input_pair()
    return make_candidate_bridge(preview, ai).candidate


class RevisionComparisonTests(unittest.TestCase):
    def test_empty_base_and_latest_keep_all_three(self):
        candidate = bridge_candidate()
        r = compare_revisions(
            candidate, base_snapshot={"projects": []},
            latest_snapshot={"projects": []}, base_revision=1, latest_revision=2,
        )
        self.assertEqual(r["source_project_count"], 3)
        self.assertEqual(r["base_diff"]["root_change_types"], {"NEW": 3})
        self.assertEqual(r["latest_diff"]["root_change_types"], {"NEW": 3})
        self.assertTrue(r["no_db_or_candidate_mutation"])

    def test_latest_same_identity_does_not_drop_candidate(self):
        candidate = bridge_candidate()
        first = candidate.projects[0]
        r = compare_revisions(
            candidate,
            base_snapshot={"projects": []},
            latest_snapshot={"projects": [{
                "id": "00000000-0000-0000-0000-000000000123",
                "project_name": first.project_name,
                "customer_name": first.customer_name,
                "start_date": first.start_date,
                "end_date": first.end_date,
            }]},
            base_revision=1, latest_revision=5,
        )
        self.assertEqual(r["latest_diff"]["represented_source_candidates"], 3)
        self.assertEqual(r["latest_diff"]["root_change_types"].get("SAME"), 1)
        # The existing Diff engine treats a different title with the same
        # customer AND period as REVIEW, not NEW. REVIEW is informational:
        # it does not silently link, merge, or drop those two source projects.
        self.assertEqual(r["latest_diff"]["root_change_types"].get("REVIEW"), 2)
        self.assertNotIn("NEW", r["latest_diff"]["root_change_types"])
        self.assertEqual(
            sorted(item["candidate_index"] for item
                   in r["latest_diff"]["root_indices_only"]),
            [0, 1, 2],
        )
        self.assertEqual(r["latest_confirmed_project_count"], 1)

    def test_latest_field_only_diffs_keep_36_like_projects_represented(self):
        candidate = bridge_candidate()
        first = candidate.projects[0]
        altered = first.model_copy(update={"project_summary": "synthetic summary"})
        full = candidate.model_copy(update={"projects": [altered, *candidate.projects[1:]]})
        r = compare_revisions(
            full,
            base_snapshot={"projects": []},
            latest_snapshot={"projects": [{
                "id": "00000000-0000-0000-0000-000000000124",
                "project_name": first.project_name,
                "customer_name": first.customer_name,
                "start_date": first.start_date,
                "end_date": first.end_date,
            }]},
            base_revision=2, latest_revision=3,
        )
        self.assertEqual(r["latest_diff"]["represented_source_candidates"], 3)
        self.assertEqual(r["latest_diff"]["project_root_diff_count"], 2)
        self.assertEqual(r["latest_diff"]["field_or_relation_diff_count"], 1)

    def test_same_name_customer_period_on_distinct_records_not_collapsed(self):
        candidate = bridge_candidate()
        first = candidate.projects[0]
        second = first.model_copy()
        combined = candidate.model_copy(
            update={"projects": [first, second, *candidate.projects[1:]]}
        )
        r = compare_revisions(
            combined,
            base_snapshot={"projects": []},
            latest_snapshot={"projects": []},
            base_revision=1, latest_revision=1,
        )
        self.assertEqual(r["source_project_count"], 4)
        self.assertEqual(r["base_diff"]["represented_source_candidates"], 4)

    def test_reject_missing_base_projects(self):
        with self.assertRaisesRegex(ValueError, "REVISION_SNAPSHOT_INVALID"):
            compare_revisions(
                bridge_candidate(),
                base_snapshot={"other": []},
                latest_snapshot={"projects": []},
                base_revision=1, latest_revision=2,
            )

    def test_reject_non_monotonic_revision(self):
        with self.assertRaisesRegex(ValueError, "REVISION_SNAPSHOT_INVALID"):
            compare_revisions(
                bridge_candidate(),
                base_snapshot={"projects": []},
                latest_snapshot={"projects": []},
                base_revision=5, latest_revision=4,
            )

    def test_canonical_sha_independent_of_key_order(self):
        a = {"a": [2, 3], "b": {"y": 4, "z": 5}}
        b = {"b": {"z": 5, "y": 4}, "a": [2, 3]}
        self.assertEqual(_fingerprint(a), _fingerprint(b))


if __name__ == "__main__":
    unittest.main()
