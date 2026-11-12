"""v30 source-first -> existing Candidate / Diff compatibility unit tests.

All fixtures are synthetic. No live DB, LLM/VLM, MinIO or secrets.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_project_source_first_candidate_v30 import fixtures
from app.modules.analysis.project_source_first_candidate_v30 import build_source_first_candidates
from app.modules.analysis.project_source_first_bridge_v30 import make_candidate_bridge
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from scripts.profile_source_first_diff_preview_noai import report_diff


def input_pair():
    source, ai = fixtures()
    # Synthetic root refs are deliberately verified by the earlier
    # classification in the fixture and point to the correct source row pages.
    ai["projects"][0]["source_refs"] = [{
        "document_id": "doc1", "page_no": 1,
        "quote_text": "비공개공공플랫폼개발",
    }]
    ai["projects"][2]["source_refs"] = [{
        "document_id": "doc1", "page_no": 1,
        "quote_text": "비공개다른사업",
    }]
    _public, private_preview = build_source_first_candidates(source, ai)
    return private_preview, ai


class BridgeTests(unittest.TestCase):
    def test_source_rows_preserved_in_existing_candidate_schema(self):
        preview, ai = input_pair()
        original = copy.deepcopy((preview, ai))
        result = make_candidate_bridge(preview, ai)
        self.assertEqual((preview, ai), original)
        self.assertEqual(len(result.candidate.projects), 3)
        self.assertEqual(result.diagnostics["name_variant_pair_count"], 1)
        self.assertEqual(len(set(p.project_name for p in result.candidate.projects)), 3)
        self.assertEqual(result.diagnostics["source_only_groups"], 1)
        self.assertEqual(result.diagnostics["ai_suggestion_count"], 3)
        self.assertEqual(result.diagnostics["unlinked_ai_count"], 1)
        self.assertTrue(result.diagnostics["all_name_variant_pairs_preserved_separately"])

    def test_root_copies_real_ref_only_for_verified_unique_candidate(self):
        preview, ai = input_pair()
        result = make_candidate_bridge(preview, ai)
        by_name = {p.project_name: p for p in result.candidate.projects}
        self.assertEqual(
            by_name["비공개공공플랫폼개발"].source_refs[0].quote_text,
            "비공개공공플랫폼개발",
        )
        self.assertEqual(by_name["비공개다른사업"].source_refs[0].page_no, 1)
        self.assertEqual(by_name["비공개명칭차이사업"].source_refs, [])
        self.assertEqual(result.diagnostics["root_original_quote_ref_groups"], 2)

    def test_ai_enrichments_and_unverified_dates_not_promoted(self):
        preview, ai = input_pair()
        result = make_candidate_bridge(preview, ai)
        root = {p.project_name: p for p in result.candidate.projects}[
            "비공개공공플랫폼개발"
        ]
        self.assertEqual((root.start_date, root.end_date), ("2020.02", "2020.04"))
        self.assertIsNone(root.responsibilities)
        self.assertIsNone(root.project_summary)
        self.assertEqual(root.jobs, [])
        self.assertEqual(root.skills, [])
        self.assertEqual(root.expertise, [])
        self.assertEqual(root.business_domains, [])

    def test_existing_diff_empty_baseline_projects_all_new(self):
        preview, ai = input_pair()
        result = make_candidate_bridge(preview, ai)
        report = report_diff(result.candidate, {"projects": []})
        self.assertEqual(report["project_root_diff_count"], 3)
        self.assertEqual(report["project_root_change_types"], {"NEW": 3})
        self.assertEqual(report["non_root_diff_count"], 0)
        self.assertEqual(report["project_root_with_literal_quote"], 2)
        self.assertEqual(report["legacy_new_bulk_approval_guard_count"], 1)
        self.assertEqual(
            [r["candidate_index"] for r in report["root_diff_indices_only"]],
            [0, 1, 2],
        )

    def test_existing_guard_preserves_projects_without_page_context(self):
        preview, ai = input_pair()
        result = make_candidate_bridge(preview, ai)
        guarded = apply_candidate_quality_guards(
            result.candidate, strict_section_authority=False,
        )
        self.assertEqual(
            [(p.project_name, p.start_date) for p in guarded.projects],
            [(p.project_name, p.start_date) for p in result.candidate.projects],
        )

    def test_reject_private_fixture_cross_run(self):
        preview, ai = input_pair()
        ai["run_id"] = "another"
        with self.assertRaisesRegex(ValueError, "BRIDGE_FIXTURE_FINGERPRINT_MISMATCH"):
            make_candidate_bridge(preview, ai)

    def test_reject_verified_ref_from_wrong_page(self):
        preview, ai = input_pair()
        ai["projects"][0]["source_refs"][0]["page_no"] = 99
        with self.assertRaisesRegex(ValueError, "BRIDGE_VERIFIED_REF_NOT_FROM_SOURCE"):
            make_candidate_bridge(preview, ai)

    def test_reject_fake_quote_when_claiming_verified(self):
        preview, ai = input_pair()
        ai["projects"][0]["source_refs"][0]["quote_text"] = ""
        with self.assertRaisesRegex(ValueError, "BRIDGE_VERIFIED_REF_NOT_FROM_SOURCE"):
            make_candidate_bridge(preview, ai)

    def test_reject_claimed_verified_when_root_identity_mismatch(self):
        preview, ai = input_pair()
        ai["projects"][0]["project_name"] = "일치하지않는타사업"
        with self.assertRaisesRegex(ValueError, "BRIDGE_SUGGESTION_IDENTITY_MISMATCH"):
            make_candidate_bridge(preview, ai)

    def test_reject_ambiguous_preferred_evidence(self):
        preview, ai = input_pair()
        row = next(p for p in preview["projects"]
                   if p["preferred_enrichment_candidate_index"] == 0)
        row["preferred_enrichment_candidate_index"] = 1
        with self.assertRaisesRegex(ValueError, "BRIDGE_PREFERRED_NOT_UNIQUE"):
            make_candidate_bridge(preview, ai)

    def test_reject_incomplete_ai_preservation(self):
        preview, ai = input_pair()
        preview["unlinked_ai_candidate_indices"] = []
        with self.assertRaisesRegex(ValueError, "BRIDGE_AI_COVERAGE_INVALID"):
            make_candidate_bridge(preview, ai)

    def test_ongoing_end_date_compatible_with_existing_schema(self):
        preview, ai = input_pair()
        row = next(x for x in preview["projects"]
                   if x["source_project"]["project_name"] == "비공개공공플랫폼개발")
        row["source_project"]["end_date"] = "현재"
        for p in row["enrichment_suggestions"]:
            p["candidate_period_matches_original_months"] = False
            p["candidate_root_evidence_verified"] = False
        row["preferred_enrichment_candidate_index"] = None
        result = make_candidate_bridge(preview, ai)
        project = next(p for p in result.candidate.projects
                       if p.project_name == "비공개공공플랫폼개발")
        self.assertEqual(project.end_date, "현재")
        self.assertEqual(project.source_refs, [])

    def test_scales_to_36_native_project_rows_without_title_merge(self):
        preview, ai = input_pair()
        first = next(x for x in preview["projects"]
                     if not x["enrichment_suggestions"])
        for i in range(33):
            row = copy.deepcopy(first)
            row["group_id"] = f"extra{i}"
            row["source_project"]["project_name"] = f"별도원본프로젝트{i}"
            row["source_project"]["original_titles"] = [f"별도원본프로젝트{i}"]
            row["name_variant_for_post_registration_optional_review"] = False
            preview["projects"].append(row)
        preview["project_count"] = 36
        result = make_candidate_bridge(preview, ai)
        self.assertEqual(len(result.candidate.projects), 36)
        self.assertEqual(result.diagnostics["name_variant_pair_count"], 1)
        summary = report_diff(result.candidate, {"projects": []})
        self.assertEqual(summary["project_root_diff_count"], 36)
        self.assertEqual(summary["project_root_change_types"], {"NEW": 36})


if __name__ == "__main__":
    unittest.main()
