"""Simple review: bulk approve everything, skip invalid values at Confirm."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.person import EmploymentHistory, PersonProfile
from app.db.models.revision import AuditLog
from app.core.exceptions import ConfirmValidationError
from app.modules.analysis.confirm import _ConfirmContext
from app.modules.evidence.materializer import AppliedTargetRegistry

pytest_plugins = ["tests.test_analysis"]


def test_confirm_skips_invalid_diff_with_savepoint_unit() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata = MetaData()
    staged = Table("staged", metadata, Column("value", String, nullable=False))
    metadata.create_all(engine)

    with Session(engine) as db:
        ctx = object.__new__(_ConfirmContext)
        ctx.db = db
        ctx.project_ids_by_index = {}
        ctx.skipped_diff_ids = set()
        ctx.skipped_paths = []
        ctx.applied = AppliedTargetRegistry()

        bad_date = AnalysisDiffItem(
            id=uuid.uuid4(),
            entity_type="EMPLOYMENT",
            candidate_path="employment_history[0]",
            change_type="NEW",
            review_status="ACCEPTED",
            new_value={"company_name": "테스트회사", "start_date": "PL"},
        )
        bad_late = AnalysisDiffItem(
            id=uuid.uuid4(),
            entity_type="PROFILE",
            candidate_path="profile.phone",
            change_type="UPDATE",
            review_status="ACCEPTED",
            new_value="010-0000-0000",
        )
        bad_department = AnalysisDiffItem(
            id=uuid.uuid4(),
            entity_type="EMPLOYMENT",
            candidate_path="employment_history[1]",
            change_type="REVIEW",
            review_status="ACCEPTED",
            new_value={"company_name": "테스트회사", "department": "PL"},
        )
        good = AnalysisDiffItem(
            id=uuid.uuid4(),
            entity_type="PROFILE",
            candidate_path="profile.department",
            change_type="UPDATE",
            review_status="ACCEPTED",
        )

        def apply_bad_date(_diff):
            raise AssertionError("Invalid date should be skipped before applying")

        def apply_bad_late(_diff):
            db.execute(insert(staged).values(value="should rollback"))
            raise ConfirmValidationError("invalid value after write")

        def apply_good(_diff):
            db.execute(insert(staged).values(value="kept"))

        ctx._apply_with_skip(bad_date, apply_bad_date)
        ctx._apply_with_skip(bad_department, apply_bad_date)
        ctx._apply_with_skip(bad_late, apply_bad_late)
        ctx._apply_with_skip(good, apply_good)
        assert db.execute(select(staged.c.value)).scalars().all() == ["kept"]
        assert ctx.skipped_diff_ids == {bad_date.id, bad_department.id, bad_late.id}
        assert ctx.skipped_paths == [
            "EMPLOYMENT employment_history[0]",
            "EMPLOYMENT employment_history[1]",
            "PROFILE profile.phone",
        ]


def test_bulk_accept_then_confirm_skips_invalid_dates(
    client: TestClient, db_session
) -> None:
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _login,
        _seed_person_with_ready_doc,
    )

    admin = _create_user(
        db_session, login_id=f"simple_{uuid.uuid4().hex[:9]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    create = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={"person_id": str(person.id), "document_ids": [str(document.id)]},
    )
    assert create.status_code in {200, 202}, create.text
    run = db_session.get(
        AnalysisRun, uuid.UUID(create.json()["data"]["analysis_id"])
    )
    assert run is not None
    run.status = "REVIEWING"
    good = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.department",
        field_name="department",
        change_type="NEW",
        new_value="개발팀",
        review_status="PENDING",
    )
    bad = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EMPLOYMENT",
        candidate_path="employment_history[0]",
        change_type="REVIEW",
        new_value={
            "company_name": "테스트회사",
            "start_date": "PL",
            "end_date": "2020.12",
            "source_refs": [],
        },
        review_status="PENDING",
    )
    db_session.add_all([run, good, bad])
    db_session.commit()

    strict = client.post(
        f"/api/v1/analyses/{run.id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(bad.id)], "review_status": "ACCEPTED"},
    )
    assert strict.status_code == 400

    bulk = client.post(
        f"/api/v1/analyses/{run.id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(good.id), str(bad.id)],
            "review_status": "ACCEPTED",
            "simple_review": True,
        },
    )
    assert bulk.status_code == 200, bulk.text
    assert all(item["review_status"] == "ACCEPTED" for item in bulk.json()["data"])

    # Even completed rows may be selected and re-decided without a reset step.
    reject = client.post(
        f"/api/v1/analyses/{run.id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(bad.id)],
            "review_status": "REJECTED",
            "simple_review": True,
        },
    )
    assert reject.status_code == 200, reject.text
    approve_again = client.post(
        f"/api/v1/analyses/{run.id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(bad.id)],
            "review_status": "ACCEPTED",
            "simple_review": True,
        },
    )
    assert approve_again.status_code == 200, approve_again.text

    confirm = client.post(
        f"/api/v1/analyses/{run.id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_profile_version": run.base_profile_version,
            "skip_invalid_values": True,
        },
    )
    assert confirm.status_code == 200, confirm.text
    result = confirm.json()["data"]
    assert result["status"] == "CONFIRMED"
    assert result["skipped_diff_count"] == 1
    assert result["skipped_diff_paths"] == ["EMPLOYMENT employment_history[0]"]

    db_session.expire_all()
    profile = db_session.get(PersonProfile, person.id)
    assert profile is not None and profile.department == "개발팀"
    assert db_session.execute(
        select(EmploymentHistory).where(EmploymentHistory.person_id == person.id)
    ).scalars().all() == []
    db_session.refresh(bad)
    assert bad.review_status == "ACCEPTED"
    assert bad.new_value["start_date"] == "PL"
    audit = db_session.execute(
        select(AuditLog)
        .where(
            AuditLog.target_id == run.id,
            AuditLog.action_type == "ANALYSIS_CONFIRM",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    ).scalar_one()
    assert audit.metadata_json["skipped_diff_count"] == 1

    _cleanup_person(db_session, person.id, admin.id)
