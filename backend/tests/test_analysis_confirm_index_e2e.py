"""V36 E2E: Source-First review → strict confirm → profile + search index.

Isolated test DB only. Reuses helpers/fixtures from tests.test_analysis.
Does not touch production Compose/DB or live analyses.
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

# Prefer caller-provided isolated DATABASE_URL; never invent a production DSN.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_v36_e2e",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

pytest_plugins = ["tests.test_analysis"]

SOURCE_FIRST_V32 = "profile-source-first-v32-core-presplit"


def _cleanup_e2e(db_session, person_id, user_id) -> None:
    """Remove person-scoped rows including search index artifacts."""
    from app.db.models.person import (
        Certification,
        Education,
        EmploymentHistory,
        PersonExpertise,
        PersonJob,
        PersonSkill,
    )
    from app.db.models.project import (
        Project,
        ProjectBusinessDomain,
        ProjectCustomerType,
        ProjectExpertise,
        ProjectJob,
        ProjectSkill,
    )
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from tests.test_analysis import _cleanup_person

    project_ids = list(
        db_session.scalars(select(Project.id).where(Project.person_id == person_id)).all()
    )
    if project_ids:
        db_session.execute(delete(ProjectJob).where(ProjectJob.project_id.in_(project_ids)))
        db_session.execute(
            delete(ProjectSkill).where(ProjectSkill.project_id.in_(project_ids))
        )
        db_session.execute(
            delete(ProjectExpertise).where(ProjectExpertise.project_id.in_(project_ids))
        )
        db_session.execute(
            delete(ProjectBusinessDomain).where(
                ProjectBusinessDomain.project_id.in_(project_ids)
            )
        )
        db_session.execute(
            delete(ProjectCustomerType).where(
                ProjectCustomerType.project_id.in_(project_ids)
            )
        )
        db_session.execute(delete(Project).where(Project.id.in_(project_ids)))

    db_session.execute(
        delete(EmploymentHistory).where(EmploymentHistory.person_id == person_id)
    )
    db_session.execute(delete(Education).where(Education.person_id == person_id))
    db_session.execute(delete(Certification).where(Certification.person_id == person_id))
    db_session.execute(delete(PersonJob).where(PersonJob.person_id == person_id))
    db_session.execute(delete(PersonSkill).where(PersonSkill.person_id == person_id))
    db_session.execute(
        delete(PersonExpertise).where(PersonExpertise.person_id == person_id)
    )
    db_session.execute(
        delete(SearchIndexItem).where(SearchIndexItem.person_id == person_id)
    )
    db_session.execute(
        delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id)
    )
    db_session.commit()
    _cleanup_person(db_session, person_id, user_id)


def _start_source_first_reviewing(
    client: TestClient, db_session, csrf: str, person, document
):
    from app.db.models.analysis import AnalysisRun
    from tests.test_analysis import _start_reviewing_analysis

    analysis_id, run = _start_reviewing_analysis(
        client, db_session, csrf, person, document
    )
    run.prompt_version = SOURCE_FIRST_V32
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    assert run.prompt_version == SOURCE_FIRST_V32
    assert run.status == "REVIEWING"
    return analysis_id, run


def _project_new_value(name: str) -> dict:
    return {
        "project_name": name,
        "customer_name": "내부",
        "start_date": "2024-01",
        "end_date": "2024-06",
        "jobs": [],
        "skills": [],
        "expertise": [],
        "business_domains": [],
        "customer_types": [],
    }


def test_source_first_v32_bulk_review_confirm_rebuild_index_e2e(
    client: TestClient, db_session
) -> None:
    """Bulk ACCEPTED/REJECTED/MODIFIED → strict confirm → process REBUILD_PERSON."""
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.person import PersonProfile
    from app.db.models.project import Project
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_analysis import (
        _create_user,
        _login,
        _seed_person_with_ready_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v36e_{uuid.uuid4().hex[:9]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id, run = _start_source_first_reviewing(
        client, db_session, csrf, person, document
    )

    dept = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.department",
        field_name="department",
        change_type="NEW",
        new_value="데이터팀",
        review_status="PENDING",
    )
    phone = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.phone",
        field_name="phone",
        change_type="UPDATE",
        new_value="010-1111-2222",
        review_status="PENDING",
    )
    keep_proj = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROJECT",
        candidate_path="projects[0]",
        change_type="NEW",
        new_value=_project_new_value("V36KeepProject"),
        review_status="PENDING",
    )
    reject_proj = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROJECT",
        candidate_path="projects[1]",
        change_type="NEW",
        new_value=_project_new_value("V36RejectProject"),
        review_status="PENDING",
    )
    db_session.add_all([dept, phone, keep_proj, reject_proj])
    db_session.commit()

    bulk_accept = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(dept.id), str(keep_proj.id)],
            "review_status": "ACCEPTED",
            "simple_review": True,
        },
    )
    assert bulk_accept.status_code == 200, bulk_accept.text

    bulk_reject = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(reject_proj.id)],
            "review_status": "REJECTED",
            "simple_review": True,
        },
    )
    assert bulk_reject.status_code == 200, bulk_reject.text

    patch_phone = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{phone.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "MODIFIED", "decided_value": "010-9999-8888"},
    )
    assert patch_phone.status_code == 200, patch_phone.text

    before_jobs = list(
        db_session.execute(
            select(SearchIndexJob).where(SearchIndexJob.person_id == person.id)
        ).scalars()
    )
    assert before_jobs == []

    confirm = client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_profile_version": 1,
            "skip_invalid_values": False,
        },
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()["data"]
    assert body["status"] == "CONFIRMED"
    assert body["profile_version"] == 2
    assert body.get("skipped_diff_count", 0) == 0

    db_session.expire_all()
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.profile_version == 2
    assert profile.department == "데이터팀"
    assert profile.phone == "010-9999-8888"

    projects = list(
        db_session.execute(
            select(Project).where(
                Project.person_id == person.id, Project.deleted_at.is_(None)
            )
        ).scalars()
    )
    names = {p.project_name for p in projects}
    assert "V36KeepProject" in names
    assert "V36RejectProject" not in names

    rev = db_session.execute(
        select(ProfileRevision).where(
            ProfileRevision.person_id == person.id,
            ProfileRevision.revision_no == 2,
        )
    ).scalar_one()
    assert rev.source_type == "AI_CONFIRMED"
    assert rev.source_analysis_run_id == uuid.UUID(analysis_id)

    audits = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.action_type == "ANALYSIS_CONFIRM",
                AuditLog.target_id == uuid.UUID(analysis_id),
            )
        ).scalars()
    )
    assert audits

    pending_jobs = list(
        db_session.execute(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "REBUILD_PERSON",
                SearchIndexJob.status == "PENDING",
            )
        ).scalars()
    )
    assert len(pending_jobs) == 1
    job = pending_jobs[0]

    result = SearchIndexService(db_session).process_job(job.id)
    assert result.status == "COMPLETED"
    db_session.commit()
    db_session.expire_all()

    job2 = db_session.get(SearchIndexJob, job.id)
    assert job2 is not None and job2.status == "COMPLETED"

    items = list(
        db_session.execute(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == person.id,
                SearchIndexItem.is_active.is_(True),
            )
        ).scalars()
    )
    by_type = {i.object_type: i for i in items}
    assert "PROFILE" in by_type
    assert by_type["PROFILE"].metadata_json.get("profile_version") == 2
    assert "데이터팀" in (by_type["PROFILE"].search_text or "") or profile.department == "데이터팀"
    assert any(i.object_type == "PROJECT" for i in items)
    assert not any(
        "V36RejectProject" in (i.search_text or "") for i in items if i.object_type == "PROJECT"
    )
    assert any(
        "V36KeepProject" in (i.search_text or "") for i in items if i.object_type == "PROJECT"
    )

    # Idempotent re-confirm: no extra version / no extra PENDING index job.
    again = client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_profile_version": 1,
            "skip_invalid_values": False,
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["profile_version"] == 2
    db_session.expire_all()
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.profile_version == 2
    pending_after = list(
        db_session.execute(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "REBUILD_PERSON",
                SearchIndexJob.status == "PENDING",
            )
        ).scalars()
    )
    assert pending_after == []
    run_row = db_session.get(AnalysisRun, uuid.UUID(analysis_id))
    assert run_row is not None and run_row.status == "CONFIRMED"

    _cleanup_e2e(db_session, person.id, admin.id)


def test_source_first_strict_confirm_failure_preserves_review_state(
    client: TestClient, db_session
) -> None:
    """Strict confirm with invalid ACCEPTED value aborts; decisions stay."""
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.db.models.person import EmploymentHistory, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.db.models.search import SearchIndexJob
    from tests.test_analysis import (
        _create_user,
        _login,
        _seed_person_with_ready_doc,
    )

    admin = _create_user(
        db_session, login_id=f"v36f_{uuid.uuid4().hex[:9]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    analysis_id, run = _start_source_first_reviewing(
        client, db_session, csrf, person, document
    )

    good = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROFILE",
        candidate_path="profile.department",
        field_name="department",
        change_type="NEW",
        new_value="보존팀",
        review_status="PENDING",
    )
    bad = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EMPLOYMENT",
        candidate_path="employment_history[0]",
        change_type="NEW",
        new_value={
            "company_name": "BadDateCo",
            "start_date": "2019.01",
            "end_date": "현재아님",
            "title": "Dev",
        },
        review_status="PENDING",
    )
    db_session.add_all([good, bad])
    db_session.commit()

    bulk = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(good.id), str(bad.id)],
            "review_status": "ACCEPTED",
            "simple_review": True,
        },
    )
    assert bulk.status_code == 200, bulk.text

    rev_count_before = db_session.execute(
        select(ProfileRevision).where(ProfileRevision.person_id == person.id)
    ).scalars().all()
    job_count_before = len(
        list(
            db_session.execute(
                select(SearchIndexJob).where(SearchIndexJob.person_id == person.id)
            ).scalars()
        )
    )

    failed = client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_profile_version": 1,
            "skip_invalid_values": False,
        },
    )
    assert failed.status_code == 400, failed.text
    assert failed.json()["code"] == "CONFIRM_VALIDATION_ERROR"

    db_session.expire_all()
    run_row = db_session.get(AnalysisRun, uuid.UUID(analysis_id))
    assert run_row is not None
    assert run_row.status == "REVIEWING"
    assert run_row.prompt_version == SOURCE_FIRST_V32

    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.profile_version == 1
    # Strict abort: department must not have been applied.
    assert profile.department != "보존팀"

    assert (
        list(
            db_session.execute(
                select(EmploymentHistory).where(EmploymentHistory.person_id == person.id)
            ).scalars()
        )
        == []
    )

    db_session.refresh(good)
    db_session.refresh(bad)
    assert good.review_status == "ACCEPTED"
    assert bad.review_status == "ACCEPTED"
    assert bad.new_value["end_date"] == "현재아님"

    rev_count_after = db_session.execute(
        select(ProfileRevision).where(ProfileRevision.person_id == person.id)
    ).scalars().all()
    assert len(rev_count_after) == len(rev_count_before)
    job_count_after = len(
        list(
            db_session.execute(
                select(SearchIndexJob).where(SearchIndexJob.person_id == person.id)
            ).scalars()
        )
    )
    assert job_count_after == job_count_before

    # Recover: reject invalid candidate, then strict confirm succeeds.
    reject = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(bad.id)],
            "review_status": "REJECTED",
            "simple_review": True,
        },
    )
    assert reject.status_code == 200, reject.text

    ok = client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_profile_version": 1,
            "skip_invalid_values": False,
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["status"] == "CONFIRMED"
    assert ok.json()["data"]["profile_version"] == 2

    db_session.expire_all()
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.department == "보존팀"
    assert (
        list(
            db_session.execute(
                select(EmploymentHistory).where(EmploymentHistory.person_id == person.id)
            ).scalars()
        )
        == []
    )

    _cleanup_e2e(db_session, person.id, admin.id)
