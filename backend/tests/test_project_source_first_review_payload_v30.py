"""Synthetic v30 review payload tests: no live DB, no private data."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_project_source_first_candidate_v30 import fixtures
from app.modules.analysis.project_source_first_candidate_v30 import (
    build_source_first_candidates,
)
from app.modules.analysis.project_source_first_review_payload_v30 import (
    assemble_review_payload,
)


def input_data():
    rows, ai = fixtures()
    ai["projects"][0]["source_refs"] = [{
        "document_id": "doc1",
        "page_no": 1,
        "quote_text": "비공개공공플랫폼개발",
    }]
    ai["projects"][2]["source_refs"] = [{
        "document_id": "doc1",
        "page_no": 1,
        "quote_text": "비공개다른사업",
    }]
    _, preview = build_source_first_candidates(rows, ai)
    core = {
        "profile": {"name": "테스트사람", "current_title": "선임"},
        "jobs": [{"code": "JOB-JAVA", "job_type": "SECONDARY"}],
        "skills": [{"code": "TECH-PYTHON"}],
        "employment_history": [{
            "company_name": "비공개 회사",
            "start_date": "2020.01",
            "end_date": "2020.03",
        }],
        "projects": [{"project_name": "LLM 중복 프로젝트"}],
        "summary": {"text": "기존 AI 핵심 정보"},
    }
    return rows, ai, preview, core


def assemble(*, latest=None, override=None):
    rows, ai, preview, core = input_data()
    if override:
        override(rows, ai, preview, core)
    result = assemble_review_payload(
        source_rows=rows,
        ai_fixture=ai,
        private_preview=preview,
        original_candidate_json=core,
        current_profile_snapshot=latest or {"projects": []},
    )
    return result, core


class ReviewPayloadTests(unittest.TestCase):
    def test_preserves_entire_nonproject_candidate(self):
        result, core = assemble()
        c = result.candidate
        self.assertEqual(c.profile.name, "테스트사람")
        self.assertEqual(c.profile.current_title, "선임")
        self.assertEqual(c.jobs[0].code, "JOB-JAVA")
        self.assertEqual(c.skills[0].code, "TECH-PYTHON")
        self.assertEqual(c.employment_history[0].company_name, "비공개 회사")
        self.assertEqual(c.summary.text, "기존 AI 핵심 정보")
        self.assertEqual(len(c.projects), 3)
        self.assertNotEqual(c.projects[0].project_name, core["projects"][0]["project_name"])
        self.assertTrue(result.diagnostics["original_non_project_sections_preserved"])

    def test_roles_from_native_pdf_become_only_literal_responsibilities(self):
        result, _ = assemble()
        self.assertEqual(result.diagnostics["source_role_text_preserved_groups"], 3)
        self.assertTrue(all(p.responsibilities == "수행역할: PL"
                            for p in result.candidate.projects))
        self.assertTrue(all(p.jobs == [] and p.skills == [] and p.expertise == []
                            for p in result.candidate.projects))
        self.assertTrue(all("AI" not in (p.project_summary or "")
                            for p in result.candidate.projects))

    def test_review_diff_covers_every_independent_source_project(self):
        result, _ = assemble()
        d = result.diagnostics
        self.assertEqual(d["project_candidate_count"], 3)
        self.assertEqual(d["project_indices_represented_in_diff"], 3)
        self.assertEqual(d["name_variant_pairs_kept_separate"], 1)
        self.assertEqual(d["source_only_project_count"], 1)
        self.assertEqual(
            len([s for s in result.diffs if s.entity_type == "PROJECT"]),
            3,
        )

    def test_source_roles_used_without_fabricating_quote(self):
        result, _ = assemble()
        by_name = {p.project_name: p for p in result.candidate.projects}
        self.assertEqual(by_name["비공개명칭차이사업"].source_refs, [])
        self.assertEqual(by_name["비공개명칭차이사업"].responsibilities, "수행역할: PL")
        self.assertEqual(result.diagnostics["root_original_quote_ref_groups"], 2)

    def test_original_inputs_unchanged_after_assembly(self):
        rows, ai, preview, core = input_data()
        frozen = copy.deepcopy((rows, ai, preview, core))
        _result = assemble_review_payload(
            source_rows=rows, ai_fixture=ai,
            private_preview=preview, original_candidate_json=core,
            current_profile_snapshot={"projects": []},
        )
        self.assertEqual((rows, ai, preview, core), frozen)

    def test_rejects_mismatched_private_source_preview(self):
        rows, ai, preview, core = input_data()
        preview["projects"][0]["source_project"]["project_name"] = "수정된 원본"
        with self.assertRaisesRegex(ValueError, "V30_REVIEW_PREVIEW_FINGERPRINT_MISMATCH"):
            assemble_review_payload(
                source_rows=rows, ai_fixture=ai,
                private_preview=preview, original_candidate_json=core,
                current_profile_snapshot={"projects": []},
            )

    def test_rejects_unparseable_stored_core(self):
        rows, ai, preview, core = input_data()
        core["profile"]["name"] = "X" * 200
        with self.assertRaisesRegex(ValueError, "V30_REVIEW_ORIGINAL_CANDIDATE_SCHEMA_INVALID"):
            assemble_review_payload(
                source_rows=rows, ai_fixture=ai,
                private_preview=preview, original_candidate_json=core,
                current_profile_snapshot={"projects": []},
            )

    def test_rejects_missing_current_snapshot(self):
        rows, ai, preview, core = input_data()
        with self.assertRaisesRegex(ValueError, "V30_REVIEW_CURRENT_SNAPSHOT_INVALID"):
            assemble_review_payload(
                source_rows=rows, ai_fixture=ai,
                private_preview=preview, original_candidate_json=core,
                current_profile_snapshot={},
            )

    def test_existing_person_profile_same_customer_date_does_not_merge(self):
        rows, ai, preview, core = input_data()
        first = preview["projects"][0]["source_project"]
        existing = {"projects": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "project_name": first["project_name"],
            "customer_name": first["customer_name"],
            "start_date": first["start_date"],
            "end_date": first["end_date"],
        }]}
        result = assemble_review_payload(
            source_rows=rows, ai_fixture=ai,
            private_preview=preview, original_candidate_json=core,
            current_profile_snapshot=existing,
        )
        self.assertEqual(len(result.candidate.projects), 3)
        self.assertEqual(result.diagnostics["project_indices_represented_in_diff"], 3)
        self.assertEqual(result.diagnostics["name_variant_pairs_kept_separate"], 1)

    def test_realistic_36_row_identity_safety(self):
        rows, ai, preview, core = input_data()
        source_only = next(x for x in rows["source_groups"]
                           if not x["candidate_indices"])
        for index in range(33):
            item = copy.deepcopy(source_only)
            item["group_id"] = f"extra-{index}"
            item["display_name_from_source"] = f"비공개프로젝트-{index}"
            item["original_project_names"] = [item["display_name_from_source"]]
            item["review_name_variant_pair"] = False
            rows["source_groups"].append(item)
        _, preview = build_source_first_candidates(rows, ai)
        r = assemble_review_payload(
            source_rows=rows, ai_fixture=ai,
            private_preview=preview, original_candidate_json=core,
            current_profile_snapshot={"projects": []},
        )
        self.assertEqual(r.diagnostics["project_candidate_count"], 36)
        self.assertEqual(r.diagnostics["project_indices_represented_in_diff"], 36)
        self.assertEqual(r.diagnostics["source_role_text_preserved_groups"], 36)
        self.assertEqual(r.diagnostics["name_variant_pairs_kept_separate"], 1)


if __name__ == "__main__":
    unittest.main()
