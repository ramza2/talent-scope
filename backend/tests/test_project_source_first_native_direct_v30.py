"""Pure in-memory native PDF row -> Source-First v30 Candidate/Diff tests.

No operational DB, S3, LLM, VLM, private source files or web requests.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.analysis.project_row_grouping import _row
from app.modules.analysis.project_source_first_native_direct_v30 import (
    assemble_native_direct,
)

COLUMNS = {"period": 0, "project_name": 1, "role": 2, "customer": 3}


def synthetic_native_rows():
    # Two different titles, identical customer/date, different source PDFs:
    # never merge these merely because they resemble the same project.
    return [
        _row(
            "doc1", 1, "pdf-native-table", 1,
            ["2020.02~2020.04", "비공개플랫폼구축사업", "PL", "비공개고객기관"],
            COLUMNS, [10, 20, 300, 45],
        ),
        _row(
            "doc2", 1, "pdf-native-table", 1,
            ["2020.02~2020.04", "비공개플랫폼구축사업2차", "PM", "비공개고객기관"],
            COLUMNS, [10, 20, 300, 45],
        ),
        _row(
            "doc1", 2, "pdf-native-table", 1,
            ["2018.01~2018.09", "비공개별도사업운영", "품질관리 PL", "비공개고객기관"],
            COLUMNS, [10, 20, 300, 45],
        ),
    ]


def original_candidate():
    return {
        "profile": {"name": "시험사용자", "current_title": "선임"},
        "jobs": [{"code": "JOB-JAVA"}],
        "skills": [{"code": "TECH-PYTHON"}],
        "employment_history": [{
            "company_name": "비공개회사", "start_date": "2018.01",
            "end_date": "2020.01",
        }],
        "projects": [
            {
                "project_name": "비공개플랫폼구축사업",
                "customer_name": "비공개고객기관",
                "start_date": "2020.02", "end_date": "2020.04",
                "responsibilities": "LLM 임의 역할",
                "source_refs": [{
                    "document_id": "doc1", "page_no": 1,
                    "quote_text": "비공개플랫폼구축사업",
                }],
            }
        ],
        "summary": {"text": "기존 AI 추출 요약"},
    }


def run_native(*, rows=None, original=None, latest=None, page_texts=None):
    return assemble_native_direct(
        native_rows=synthetic_native_rows() if rows is None else rows,
        original_candidate_json=original_candidate() if original is None else original,
        latest_confirmed_snapshot={"projects": []} if latest is None else latest,
        page_texts={
            ("doc1", 1): "비공개플랫폼구축사업 프로젝트 원문",
            ("doc2", 1): "비공개플랫폼구축사업2차 사업",
            ("doc1", 2): "비공개별도사업운영",
        } if page_texts is None else page_texts,
    )


class NativeDirectTests(unittest.TestCase):
    def test_three_native_rows_create_three_distinct_projects(self):
        r = run_native()
        self.assertEqual(r.diagnostics["native_pdf_input_rows"], 3)
        self.assertEqual(r.diagnostics["candidate_project_count"], 3)
        self.assertEqual(r.diagnostics["represented_project_candidates"], 3)
        self.assertEqual(
            r.diagnostics["name_variant_review_suggestions_kept_separate"], 1
        )
        self.assertEqual(len(set(p.project_name for p in r.candidate.projects)), 3)
        self.assertEqual(r.diagnostics["project_diff_type_counts"], {"NEW": 3})

    def test_original_roles_preserved_without_making_up_codes(self):
        r = run_native()
        self.assertEqual(
            {p.responsibilities for p in r.candidate.projects},
            {"수행역할: PL", "수행역할: PM", "수행역할: 품질관리 PL"},
        )
        self.assertEqual(r.diagnostics["source_roles_preserved_count"], 3)
        self.assertTrue(all(p.jobs == [] and p.skills == [] for p in r.candidate.projects))

    def test_nonproject_sections_unchanged(self):
        raw = original_candidate()
        initial = copy.deepcopy(raw)
        rows = synthetic_native_rows()
        rows_initial = copy.deepcopy(rows)
        r = run_native(rows=rows, original=raw)
        self.assertEqual(raw, initial)
        self.assertEqual(rows, rows_initial)
        self.assertEqual(r.candidate.profile.current_title, "선임")
        self.assertEqual(r.candidate.skills[0].code, "TECH-PYTHON")
        self.assertEqual(r.candidate.jobs[0].code, "JOB-JAVA")
        self.assertEqual(r.candidate.summary.text, "기존 AI 추출 요약")
        self.assertTrue(r.diagnostics["nonproject_candidate_unchanged"])

    def test_provenance_only_from_literal_root_quote_owned_page(self):
        r = run_native()
        project = next(p for p in r.candidate.projects
                       if p.project_name == "비공개플랫폼구축사업")
        self.assertEqual(project.source_refs[0].quote_text, "비공개플랫폼구축사업")
        self.assertEqual(r.diagnostics["native_group_root_quotes_reverified_count"], 1)

    def test_wrong_original_page_text_does_not_fabricate_ref(self):
        r = run_native(page_texts={("doc1", 1): "인용문이 없는 원문"})
        self.assertEqual(
            r.diagnostics["native_group_root_quotes_reverified_count"], 0
        )
        self.assertTrue(all(not p.source_refs for p in r.candidate.projects))
        self.assertEqual(r.diagnostics["represented_project_candidates"], 3)

    def test_already_confirmed_same_project_and_name_variant_can_coexist(self):
        r = run_native(latest={"projects": [{
            "id": "00000000-0000-0000-0000-000000000111",
            "project_name": "비공개플랫폼구축사업",
            "customer_name": "비공개고객기관",
            "start_date": "2020.02", "end_date": "2020.04",
        }]})
        self.assertEqual(r.diagnostics["candidate_project_count"], 3)
        self.assertEqual(r.diagnostics["represented_project_candidates"], 3)
        # Exact existing identity gets a field-level UPDATE because v30
        # adds the original role. The similar-but-different title must NOT
        # update the same existing record: v30 forces a root REVIEW instead.
        self.assertEqual(r.diagnostics["project_diff_type_counts"]["UPDATE"], 1)
        self.assertEqual(r.diagnostics["project_diff_type_counts"]["REVIEW"], 1)
        self.assertEqual(r.diagnostics["project_diff_type_counts"]["NEW"], 1)
        self.assertEqual(r.diagnostics["title_variant_auto_links_blocked"], 1)
        variant_idx = next(
            i for i, p in enumerate(r.candidate.projects)
            if p.project_name == "비공개플랫폼구축사업2차"
        )
        variant = [s for s in r.specs
                   if s.candidate_path == f"projects[{variant_idx}]"]
        self.assertEqual(len(variant), 1)
        self.assertEqual(variant[0].change_type, "REVIEW")
        self.assertIsNone(variant[0].existing_target_id)

    def test_no_fuzzy_match_when_confirmed_project_already_has_role(self):
        r = run_native(latest={"projects": [{
            "id": "00000000-0000-0000-0000-000000000111",
            "project_name": "비공개플랫폼구축사업",
            "customer_name": "비공개고객기관",
            "start_date": "2020.02", "end_date": "2020.04",
            "responsibilities": "수행역할: PL",
        }]})
        self.assertEqual(r.diagnostics["project_diff_type_counts"],
                         {"SAME": 1, "REVIEW": 1, "NEW": 1})
        self.assertEqual(r.diagnostics["title_variant_auto_links_blocked"], 1)

    def test_multiple_exact_native_rows_never_update_same_existing_target(self):
        rows = synthetic_native_rows()
        duplicate = copy.deepcopy(rows[0])
        duplicate["row_index"] = 2
        duplicate["row_id"] = "a-separate-native-row"
        rows.append(duplicate)
        result = run_native(rows=rows, latest={"projects": [{
            "id": "00000000-0000-0000-0000-000000000111",
            "project_name": "비공개플랫폼구축사업",
            "customer_name": "비공개고객기관",
            "start_date": "2020.02", "end_date": "2020.04",
        }]})
        self.assertEqual(result.diagnostics["candidate_project_count"], 4)
        self.assertEqual(result.diagnostics["represented_project_candidates"], 4)
        self.assertEqual(
            result.diagnostics["duplicate_target_auto_links_blocked"], 2
        )
        self.assertEqual(result.diagnostics["title_variant_auto_links_blocked"], 1)
        self.assertEqual(result.diagnostics["project_diff_type_counts"],
                         {"REVIEW": 3, "NEW": 1})
        self.assertTrue(all(
            s.existing_target_id is None
            for s in result.specs if s.entity_type == "PROJECT"
        ))

    def test_exact_match_stays_update_without_collision(self):
        rows = synthetic_native_rows()
        first_only = [rows[0], rows[2]]
        r = run_native(rows=first_only, latest={"projects": [{
            "id": "00000000-0000-0000-0000-000000000111",
            "project_name": "비공개플랫폼구축사업",
            "customer_name": "비공개고객기관",
            "start_date": "2020.02", "end_date": "2020.04",
        }]})
        self.assertEqual(r.diagnostics["project_diff_type_counts"],
                         {"UPDATE": 1, "NEW": 1})
        self.assertEqual(r.diagnostics["title_variant_auto_links_blocked"], 0)
        self.assertEqual(r.diagnostics["duplicate_target_auto_links_blocked"], 0)

    def test_invalid_geometry_fails_closed(self):
        rows = synthetic_native_rows()
        rows[0]["bbox"] = None
        with self.assertRaisesRegex(
            ValueError, "NATIVE_V30_UNVERIFIED_OR_AMBIGUOUS_SOURCE_ROWS"
        ):
            run_native(rows=rows)

    def test_missing_pdf_project_customer_fails_closed(self):
        rows = synthetic_native_rows()
        rows[0]["fields"]["customer"] = ""
        with self.assertRaisesRegex(
            ValueError, "NATIVE_V30_UNVERIFIED_OR_AMBIGUOUS_SOURCE_ROWS"
        ):
            run_native(rows=rows)

    def test_repeated_same_document_row_kept_separately(self):
        rows = synthetic_native_rows()
        copied = copy.deepcopy(rows[0])
        copied["row_index"] = 2
        copied["row_id"] = "distinct-row"
        rows.append(copied)
        r = run_native(rows=rows)
        self.assertEqual(r.diagnostics["candidate_project_count"], 4)
        self.assertEqual(r.diagnostics["represented_project_candidates"], 4)
        self.assertEqual(
            r.diagnostics["source_status_counts"].get(
                "ambiguous_same_document_key"
            ), 2,
        )

    def test_public_output_is_only_counts_and_indices(self):
        r = run_native()
        summary = str(r.diagnostics)
        for private in ("비공개", "시험사용자", "수행역할:", "품질관리 PL"):
            self.assertNotIn(private, summary)
        self.assertEqual(len(r.private_source_provenance), 3)

    def test_no_native_rows_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "NATIVE_V30_NO_ROWS"):
            run_native(rows=[])

    def test_scales_to_36_source_projects_without_ai_fallback(self):
        rows = synthetic_native_rows()
        for i in range(33):
            rows.append(_row(
                "doc1", 10 + i, "pdf-native-table", i + 1,
                ["2017.01~2017.06", f"비공개추가프로젝트{i:02d}", "개발", "비공개고객기관"],
                COLUMNS, [10, 20, 300, 45],
            ))
        r = run_native(rows=rows)
        self.assertEqual(r.diagnostics["native_project_groups"], 36)
        self.assertEqual(r.diagnostics["represented_project_candidates"], 36)
        self.assertEqual(r.diagnostics["source_roles_preserved_count"], 36)
        self.assertEqual(len(r.private_source_provenance), 36)


if __name__ == "__main__":
    unittest.main()
