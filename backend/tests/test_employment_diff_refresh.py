"""A stored REVIEWING candidate may only gain safe NEW -> REVIEW labels."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.ai.schemas.profile_candidate import (
    EmploymentCandidate,
    ProfileCandidateDocument,
)
from app.core.exceptions import AnalysisStateConflictError
from app.modules.analysis.diff_engine import build_diffs
from app.modules.analysis.employment_diff_refresh import (
    apply_employment_diff_refresh,
    plan_employment_diff_refresh,
    preview_employment_diff_refresh,
)


def _fixture():
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="㈜씨에이에스", start_date="2023.07.03", end_date="현재"
            ),
            EmploymentCandidate(
                company_name="㈜신규", start_date="2020.01", end_date="2020.05"
            ),
        ]
    )
    snapshot = {
        "employment_history": [
            {
                "id": str(uuid4()),
                "company_name": "㈜씨에이에스",
                "start_date": "2023-07-01",
                "end_date": None,
                "department": None,
                "title": None,
                "responsibilities": None,
            }
        ]
    }
    recomputed = [
        spec for spec in build_diffs(candidate, snapshot)
        if spec.entity_type == "EMPLOYMENT"
    ]
    assert [item.change_type for item in recomputed] == ["REVIEW", "NEW"]
    saved = []
    for spec in recomputed:
        persisted = spec.to_persist_dict()
        saved.append(
            SimpleNamespace(
                id=uuid4(),
                candidate_path=spec.candidate_path,
                field_name=spec.field_name,
                change_type="NEW",  # legacy Diff classified both as NEW
                review_status="PENDING",
                old_value=None,
                existing_target_id=None,
                new_value=persisted["new_value"],
            )
        )
    return candidate, snapshot, recomputed, saved


def test_planner_updates_only_month_match_and_preserves_other_rows():
    _candidate, _snapshot, recomputed, saved = _fixture()
    first_id, second_id = saved[0].id, saved[1].id
    plan = plan_employment_diff_refresh(saved, recomputed)

    assert plan.before_counts == {"NEW": 2}
    assert plan.after_counts == {"REVIEW": 1, "NEW": 1}
    assert len(plan.changes) == 1
    assert plan.changes[0][0] is saved[0]
    assert plan.changes[0][1].old_value["company_name"] == "㈜씨에이에스"
    assert (saved[0].id, saved[1].id) == (first_id, second_id)
    assert [row.change_type for row in saved] == ["NEW", "NEW"]


def test_planner_is_idempotent_after_review_transition():
    _candidate, _snapshot, recomputed, saved = _fixture()
    saved[0].change_type = "REVIEW"
    saved[0].old_value = recomputed[0].old_value
    saved[0].existing_target_id = recomputed[0].existing_target_id

    plan = plan_employment_diff_refresh(saved, recomputed)
    assert not plan.changes
    assert plan.before_counts == plan.after_counts


@pytest.mark.parametrize("mutation", ["decided", "payload", "unexpected", "duplicate"])
def test_planner_fail_closed_for_unsafe_diff_mutation(mutation):
    _candidate, _snapshot, recomputed, saved = _fixture()
    if mutation == "decided":
        saved[0].review_status = "ACCEPTED"
    elif mutation == "payload":
        saved[0].new_value = {"company_name": "tampered"}
    elif mutation == "unexpected":
        saved[0].change_type = "SAME"
    else:
        saved[1].candidate_path = saved[0].candidate_path

    with pytest.raises(AnalysisStateConflictError):
        plan_employment_diff_refresh(saved, recomputed)


class _FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_apply_preserves_diff_identity_candidate_and_unrelated_review(monkeypatch):
    import app.modules.analysis.employment_diff_refresh as mod

    candidate, snapshot, _recomputed, saved = _fixture()
    run = SimpleNamespace(
        id=uuid4(),
        person_id=uuid4(),
        status="REVIEWING",
        base_profile_version=2,
        candidate_json=candidate.to_storage_dict(),
        updated_at=None,
    )
    unrelated = SimpleNamespace(id=uuid4(), change_type="CONFLICT", review_status="MODIFIED")
    person = SimpleNamespace(deleted_at=None, status="ACTIVE")
    profile = SimpleNamespace(profile_version=2)
    events = []

    class _Repo:
        def __init__(self, db):
            self.db = db

        def get_run(self, _id, *, for_update=False):
            events.append(("run", for_update))
            return run

        def get_person(self, _id, *, for_update=False):
            events.append(("person", for_update))
            return person

        def get_profile(self, _id, *, for_update=False):
            events.append(("profile", for_update))
            return profile

        def get_profile_revision_snapshot(self, _id, revision):
            assert revision == 2
            return snapshot

        def list_diffs(self, _id, *, entity_type):
            assert entity_type == "EMPLOYMENT"
            return saved

        def add_audit(self, **kwargs):
            events.append(("audit", kwargs))

    monkeypatch.setattr(mod, "AnalysisRepository", _Repo)
    db = _FakeDb()
    actor_id = uuid4()
    original_candidate = run.candidate_json.copy()
    original_ids = [row.id for row in saved]

    preview = preview_employment_diff_refresh(db, run.id)
    assert preview.changed_paths == ("employment_history[0]",)
    assert not preview.applied
    assert not db.added and db.commits == 0

    result = apply_employment_diff_refresh(db, run.id, actor_user_id=actor_id)
    assert result.applied
    assert result.changed_paths == ("employment_history[0]",)
    assert db.commits == 1
    assert [row.id for row in saved] == original_ids
    assert saved[0].change_type == "REVIEW"
    assert saved[0].review_status == "PENDING"
    assert saved[1].change_type == "NEW"
    assert unrelated.review_status == "MODIFIED"
    assert run.candidate_json == original_candidate
    assert events[-4:-1] == [
        ("run", True), ("person", True), ("profile", True)
    ]
    assert events[-1][0] == "audit"
    assert events[-1][1]["actor_user_id"] == actor_id

    # Reapplying the same snapshot must not modify rows or log another audit.
    second = apply_employment_diff_refresh(db, run.id, actor_user_id=actor_id)
    assert not second.applied
    assert db.commits == 1
    assert db.rollbacks == 1


def test_apply_rejects_version_drift_without_write(monkeypatch):
    import app.modules.analysis.employment_diff_refresh as mod

    candidate, _snapshot, _recomputed, _saved = _fixture()
    run = SimpleNamespace(
        id=uuid4(), person_id=uuid4(), status="REVIEWING",
        base_profile_version=2, candidate_json=candidate.to_storage_dict()
    )

    class _Repo:
        def __init__(self, db):
            pass

        def get_run(self, *_a, **_k):
            return run

        def get_person(self, *_a, **_k):
            return SimpleNamespace(deleted_at=None, status="ACTIVE")

        def get_profile(self, *_a, **_k):
            return SimpleNamespace(profile_version=3)

    monkeypatch.setattr(mod, "AnalysisRepository", _Repo)
    db = _FakeDb()
    with pytest.raises(AnalysisStateConflictError):
        apply_employment_diff_refresh(db, run.id, actor_user_id=uuid4())
    assert db.commits == 0
    assert db.rollbacks == 1


def test_refresh_routes_require_admin_and_apply_requires_csrf():
    from fastapi.routing import APIRoute

    from app.modules.analysis.router import router
    from app.modules.auth.dependencies import require_admin, require_csrf

    endpoints = {
        route.path: route
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    prefix = "/analyses/{analysis_id}/employment-diff-refresh"
    preview = endpoints[f"{prefix}/preview"]
    apply = endpoints[f"{prefix}/apply"]

    assert preview.methods == {"GET"}
    assert apply.methods == {"POST"}
    assert require_admin in {dep.call for dep in preview.dependant.dependencies}
    assert require_admin in {dep.call for dep in apply.dependant.dependencies}
    assert require_csrf in {dep.call for dep in apply.dependant.dependencies}
    assert require_csrf not in {dep.call for dep in preview.dependant.dependencies}
