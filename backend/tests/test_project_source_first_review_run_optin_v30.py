"""v30 opt-in review-run materialization guards (synthetic data, no DB writes)."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_project_source_first_native_direct_v30 import run_native
from app.modules.analysis.diff_engine import build_diffs
from app.modules.analysis.project_source_first_review_run_v30 import (
    create_plan, fingerprint, persist_reviewing_run,
)
from scripts.profile_source_first_review_run_optin import verify_execute_gate


class ReviewRunOptinTests(unittest.TestCase):
    def setUp(self):
        self.original_run_id = uuid4()
        self.person_id = uuid4()
        self.documents = (uuid4(), uuid4())
        self.latest = {"projects": []}
        self.preview = run_native()
        self.plan = self.build_plan()

    def build_plan(self, *, latest=None, specs=None, group_count=3,
                   role_group_count=3):
        latest = self.latest if latest is None else latest
        return create_plan(
            original_run_id=self.original_run_id,
            original_candidate_sha="a" * 64,
            person_id=self.person_id,
            document_ids=self.documents,
            base_profile_version=8,
            latest_snapshot=latest,
            candidate=self.preview.candidate,
            diff_specs=self.preview.specs if specs is None else specs,
            source_group_count=group_count,
            role_group_count=role_group_count,
            blocked_variant_links=0,
            blocked_collision_links=0,
        )

    def test_default_is_read_only_and_ack_cannot_be_implicit(self):
        verify_execute_gate(False, "")
        with self.assertRaisesRegex(ValueError, "ACKNOWLEDGEMENT"):
            verify_execute_gate(True, "")
        with self.assertRaisesRegex(ValueError, "ACKNOWLEDGEMENT"):
            verify_execute_gate(False, "CREATE_V30_REVIEWING_RUN")
        with self.assertRaisesRegex(ValueError, "ACKNOWLEDGEMENT"):
            verify_execute_gate(True, "CREATE_V30_REVIEWING_RUN ")
        verify_execute_gate(True, "CREATE_V30_REVIEWING_RUN")

    def test_plan_preserves_three_source_projects_and_nonproject_core(self):
        self.assertEqual(self.plan.source_group_count, 3)
        self.assertEqual(len(self.plan.candidate.projects), 3)
        self.assertEqual(self.plan.role_group_count, 3)
        self.assertEqual(self.plan.candidate.profile.current_title, "선임")
        self.assertEqual(len(self.plan.diff_specs), len(self.preview.specs))

    def test_reject_incomplete_project_roles_or_count(self):
        for n, roles in [(3, 2), (2, 3), (4, 4)]:
            with self.subTest(project_count=n, roles=roles):
                with self.assertRaisesRegex(
                    ValueError, "V30_RUN_PLAN_INCOMPLETE_SOURCE_GROUPS"
                ):
                    self.build_plan(group_count=n, role_group_count=roles)

    def test_reject_unprotected_legacy_project_auto_link_diffs(self):
        latest = {"projects": [{
            "id": "00000000-0000-0000-0000-000000000111",
            "project_name": "비공개플랫폼구축사업",
            "customer_name": "비공개고객기관",
            "start_date": "2020.02",
            "end_date": "2020.04",
        }]}
        result = run_native(latest=latest)
        legacy = build_diffs(result.candidate, latest)
        with self.assertRaisesRegex(ValueError, "V30_RUN_PLAN_DIFF_NOT_SOURCE_FIRST_SAFE"):
            create_plan(
                original_run_id=self.original_run_id,
                original_candidate_sha="a" * 64,
                person_id=self.person_id,
                document_ids=self.documents,
                base_profile_version=8,
                latest_snapshot=latest,
                candidate=result.candidate,
                diff_specs=legacy,
                source_group_count=3,
                role_group_count=3,
                blocked_variant_links=1,
                blocked_collision_links=0,
            )

    def test_reject_duplicate_document_binding(self):
        with self.assertRaisesRegex(ValueError, "V30_RUN_PLAN_INPUT_INVALID"):
            create_plan(
                original_run_id=self.original_run_id,
                original_candidate_sha="a" * 64,
                person_id=self.person_id,
                document_ids=(self.documents[0], self.documents[0]),
                base_profile_version=8, latest_snapshot=self.latest,
                candidate=self.preview.candidate, diff_specs=self.preview.specs,
                source_group_count=3, role_group_count=3,
                blocked_variant_links=0, blocked_collision_links=0,
            )

    def fake_writer(self, *, profile_version=8, source_candidate=None,
                    source_status="CONFIRMED", blocking=None, previous=None,
                    docs_ready=True, snapshot=None):
        repo = MagicMock()
        repo.get_person.return_value = SimpleNamespace(
            status="ACTIVE", deleted_at=None
        )
        repo.get_profile.return_value = SimpleNamespace(
            profile_version=profile_version
        )
        repo.get_manual_create_blocking_run.return_value = blocking
        repo.get_profile_revision_snapshot.return_value = (
            self.latest if snapshot is None else snapshot
        )
        repo.get_run.return_value = SimpleNamespace(
            id=self.original_run_id,
            person_id=self.person_id,
            status=source_status,
            candidate_json=(
                {"project_fixture": "original"} if source_candidate is None
                else source_candidate
            ),
        )
        repo.list_run_document_ids.return_value = list(self.documents)
        repo.list_documents_by_ids.return_value = [
            (SimpleNamespace(processing_status="READY" if docs_ready else "FAILED"),
             SimpleNamespace(person_id=self.person_id), None)
            for _ in self.documents
        ]
        new_run_id = uuid4()
        repo.create_run.return_value = SimpleNamespace(
            id=new_run_id, status="REVIEWING",
        )
        db = MagicMock()
        db.execute.return_value.scalar_one_or_none.return_value = previous
        candidate = {"project_fixture": "original"}
        plan = self.plan.__class__(
            **{
                **self.plan.__dict__,
                "original_candidate_sha": fingerprint(candidate),
            }
        )
        return plan, repo, db, new_run_id

    def test_persist_inserts_one_reviewing_run_and_diffs_after_checks(self):
        plan, repo, db, generated = self.fake_writer()
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ):
            created = persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        self.assertEqual(created, generated)
        self.assertEqual(repo.create_run.call_count, 1)
        self.assertEqual(repo.replace_diffs.call_count, 1)
        self.assertEqual(repo.mark_reviewing.call_count, 1)
        self.assertEqual(repo.add_run_documents.call_count, 1)
        self.assertEqual(repo.add_audit.call_count, 1)
        self.assertEqual(repo.create_run.call_args.kwargs["status"], "REVIEWING")
        self.assertEqual(repo.create_run.call_args.kwargs["llm_model"], None)
        self.assertEqual(repo.create_run.call_args.kwargs["vlm_model"], None)

    def test_stale_revision_aborts_before_any_insert(self):
        plan, repo, db, _ = self.fake_writer(profile_version=9)
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "PROFILE_VERSION_DRIFT"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        repo.create_run.assert_not_called()
        repo.replace_diffs.assert_not_called()

    def test_active_analysis_aborts_before_any_insert(self):
        plan, repo, db, _ = self.fake_writer(blocking=SimpleNamespace(id=uuid4()))
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "ACTIVE_ANALYSIS_EXISTS"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        repo.create_run.assert_not_called()

    def test_duplicate_review_run_aborts_before_any_insert(self):
        plan, repo, db, _ = self.fake_writer(previous=uuid4())
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "RUN_ALREADY_EXISTS"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        repo.create_run.assert_not_called()

    def test_source_candidate_drift_aborts(self):
        plan, repo, db, _ = self.fake_writer(source_candidate={"updated": True})
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "SOURCE_ANALYSIS_CHANGED"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        repo.create_run.assert_not_called()

    def test_missing_ready_documents_aborts(self):
        plan, repo, db, _ = self.fake_writer(docs_ready=False)
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "DOCUMENT_NOT_READY"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=3
            )
        repo.create_run.assert_not_called()

    def test_explicit_revision_and_project_ack_required(self):
        plan, repo, db, _ = self.fake_writer()
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "REVISION_NOT_ACKNOWLEDGED"):
            persist_reviewing_run(
                db, plan, expected_revision=7, expected_project_count=3
            )
        with patch(
            "app.modules.analysis.project_source_first_review_run_v30.AnalysisRepository",
            return_value=repo
        ), self.assertRaisesRegex(ValueError, "COUNT_NOT_ACKNOWLEDGED"):
            persist_reviewing_run(
                db, plan, expected_revision=8, expected_project_count=2
            )
        repo.create_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
