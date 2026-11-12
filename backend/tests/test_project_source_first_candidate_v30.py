"""Source-First 36-row architecture contract tests (all synthetic)."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_project_source_first_rows_noai import inputs
from scripts.profile_project_source_first_rows_noai import build_rows
from app.modules.analysis.project_source_first_candidate_v30 import (
    build_source_first_candidates,
)


def fixtures():
    clusters, prior = inputs()
    _redacted, private_rows = build_rows(clusters, prior)
    fixture = {
        "kind": "talentscope_readonly_project_candidate_fixture",
        "run_id": "run",
        "source_sha256": "sourcehash",
        "existing_candidate_sha256": "candidatehash",
        "projects": [
            {
                "project_name": "비공개공공플랫폼개발", "customer_name": "비공개고객기관",
                "start_date": "2020.02", "end_date": "2020.04",
                "responsibilities": "AI 제안 업무 A",
                "jobs": [{"code": "JOB-PL", "source_refs": [
                    {"document_id": "doc1", "page_no": 1, "quote_text": "비공개"}
                ]}],
            },
            {
                "project_name": "비공개공공플랫폼개발", "customer_name": "비공개고객기관",
                "start_date": "2020.03", "end_date": "2020.04",
                "responsibilities": "AI 충돌하는 업무",
            },
            {
                "project_name": "비공개다른사업", "customer_name": "비공개고객기관",
                "start_date": "2020.02", "end_date": "2020.04",
                "skills": [{"code": "TECH-JAVA"}],
            },
            {
                "project_name": "비공개미등록사업", "customer_name": "비공개고객기관",
                "start_date": "2018.01", "end_date": "2019.01",
            },
        ],
    }
    return private_rows, fixture


class SourceFirstCandidateTests(unittest.TestCase):
    def test_every_source_group_is_independent_project_even_name_variant_pair(self):
        source, ai = fixtures()
        old = copy.deepcopy((source, ai))
        public, private = build_source_first_candidates(source, ai)
        self.assertEqual((source, ai), old)
        self.assertEqual(public["proposed_project_count"], 3)
        self.assertEqual(len(private["projects"]), 3)
        self.assertEqual(public["name_variant_pair_count"], 1)
        self.assertEqual(public["name_variant_groups_kept_separate"], 2)
        self.assertEqual(len({p["group_id"] for p in private["projects"]}), 3)
        self.assertTrue(all(not p["requires_pre_registration_name_merge"]
                            for p in private["projects"]))
        self.assertTrue(all(not p["automatic_confirmation"]
                            for p in private["projects"]))

    def test_empty_ai_group_still_has_original_source_project(self):
        source, ai = fixtures()
        public, private = build_source_first_candidates(source, ai)
        rows = {p["group_id"]: p for p in private["projects"]}
        self.assertEqual(public["source_only_project_count"], 1)
        self.assertEqual(rows["g3"]["source_project"]["project_name"], "비공개명칭차이사업")
        self.assertEqual(rows["g3"]["enrichment_suggestions"], [])
        self.assertTrue(rows["g3"]["suitable_for_user_bulk_review_preview"])

    def test_original_name_customer_period_and_role_cannot_be_overridden(self):
        source, ai = fixtures()
        ai["projects"][0]["project_name"] = "AI가 잘못 추론한 이름"
        ai["projects"][0]["customer_name"] = "AI가 잘못 추론한 고객"
        ai["projects"][0]["start_date"] = "2000.01"
        ai["projects"][0]["jobs"] = [{"code": "JOB-PM"}]
        _p, private = build_source_first_candidates(source, ai)
        row = next(x for x in private["projects"] if x["group_id"] == "g1")
        raw = row["source_project"]
        self.assertEqual(raw["project_name"], "비공개공공플랫폼개발")
        self.assertEqual(raw["customer_name"], "비공개고객기관")
        self.assertEqual((raw["start_date"], raw["end_date"]), ("2020.02", "2020.04"))
        self.assertEqual(raw["roles_from_pdf_row"], ["PL"])
        self.assertFalse(row["enrichment_suggestions"][0]["candidate_root_evidence_verified"])

    def test_no_unverified_ai_details_promoted_to_project_fields(self):
        source, ai = fixtures()
        _p, private = build_source_first_candidates(source, ai)
        row = next(x for x in private["projects"] if x["group_id"] == "g1")
        self.assertNotIn("responsibilities", row["source_project"])
        self.assertNotIn("jobs", row["source_project"])
        proposal = row["enrichment_suggestions"][0]["proposed_enrichment_not_automatically_accepted"]
        self.assertEqual(proposal["responsibilities"], "AI 제안 업무 A")
        self.assertEqual(proposal["jobs"][0]["code"], "JOB-PL")
        self.assertEqual(proposal["jobs"][0]["source_refs"][0]["quote_text"], "비공개")

    def test_preferred_ai_candidate_only_if_unique_root_verified_and_matching_months(self):
        source, ai = fixtures()
        public, private = build_source_first_candidates(source, ai)
        row = next(x for x in private["projects"] if x["group_id"] == "g1")
        self.assertEqual(row["preferred_enrichment_candidate_index"], 0)
        self.assertEqual(public["verified_root_candidate_count"], 2)
        self.assertEqual(public["candidate_enrichment_suggestion_count"], 3)
        self.assertEqual(public["unlinked_ai_candidate_count"], 1)

    def test_multiple_verified_roots_select_none_not_arbitrary_winner(self):
        source, ai = fixtures()
        source["source_groups"][0]["candidate_evidence_reasons"][1] = "fully_evidence_bound"
        ai["projects"][1]["start_date"] = "2020.02"
        _p, private = build_source_first_candidates(source, ai)
        g1 = next(x for x in private["projects"] if x["group_id"] == "g1")
        self.assertIsNone(g1["preferred_enrichment_candidate_index"])

    def test_public_output_never_contains_candidate_text_or_source_titles(self):
        source, ai = fixtures()
        public, _private = build_source_first_candidates(source, ai)
        output = str(public)
        for forbidden in ("비공개", "AI 제안 업무", "JOB-PL", "2020.02"):
            self.assertNotIn(forbidden, output)

    def test_fails_on_duplicate_candidate_index(self):
        source, ai = fixtures()
        source["source_groups"][1]["candidate_indices"] = [1]
        with self.assertRaisesRegex(ValueError, "AI_CANDIDATE_LINKS_INVALID"):
            build_source_first_candidates(source, ai)

    def test_fails_on_missing_ai_candidate_index(self):
        source, ai = fixtures()
        source["unmatched_candidate_indices"] = []
        with self.assertRaisesRegex(ValueError, "AI_CANDIDATE_COVERAGE_INVALID"):
            build_source_first_candidates(source, ai)

    def test_fails_on_name_variant_flag_mismatch(self):
        source, ai = fixtures()
        source["source_groups"][1]["review_name_variant_pair"] = False
        with self.assertRaisesRegex(ValueError, "NAME_VARIANT_REVIEW_FLAGS_MISMATCH"):
            build_source_first_candidates(source, ai)

    def test_fails_on_missing_native_provenance(self):
        source, ai = fixtures()
        source["source_groups"][0]["source_row_evidence"] = []
        with self.assertRaisesRegex(ValueError, "SOURCE_ROW_SCHEMA_INVALID"):
            build_source_first_candidates(source, ai)

    def test_fails_on_foreign_run(self):
        source, ai = fixtures()
        ai["run_id"] = "other-run"
        with self.assertRaisesRegex(ValueError, "SOURCE_FIRST_FIXTURE_MISMATCH"):
            build_source_first_candidates(source, ai)


if __name__ == "__main__":
    unittest.main()
