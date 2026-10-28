"""Regression: ungrounded root JOB/EXP NEW cannot bulk ACCEPTED."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.modules.analysis.diff_evidence import (
    diff_has_original_evidence,
    is_root_job_or_exp_new,
    is_ungrounded_root_job_or_exp_new,
)

# Reuse the same client/db fixtures as test_analysis (defined in that module).
pytest_plugins = ["tests.test_analysis"]


def test_diff_evidence_helpers_unit() -> None:
    assert is_root_job_or_exp_new(
        entity_type="JOB",
        change_type="NEW",
        candidate_path="jobs[0]",
    )
    assert is_root_job_or_exp_new(
        entity_type="EXP",
        change_type="NEW",
        candidate_path="expertise[2]",
    )
    assert not is_root_job_or_exp_new(
        entity_type="JOB",
        change_type="NEW",
        field_name="jobs",
        candidate_path="projects[0].jobs[0]",
    )
    assert not is_root_job_or_exp_new(
        entity_type="PROJECT",
        change_type="NEW",
        candidate_path="projects[0]",
    )
    assert not diff_has_original_evidence(new_value={"code": "JOB-X", "source_refs": []})
    assert not diff_has_original_evidence(
        new_value={"code": "JOB-X", "source_refs": [{"document_id": "d", "page_no": 1}]}
    )
    assert diff_has_original_evidence(
        new_value={
            "code": "JOB-X",
            "source_refs": [{"quote_text": "원문 직무"}],
        }
    )
    assert diff_has_original_evidence(
        new_value={"code": "JOB-X"},
        evidence=[{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}],
    )
    assert is_ungrounded_root_job_or_exp_new(
        entity_type="EXP",
        change_type="NEW",
        candidate_path="expertise[0]",
        new_value={"code": "EXP-RAG", "source_refs": []},
    )


def test_bulk_accepted_rejects_ungrounded_root_job_and_exp(
    client: TestClient, db_session
) -> None:
    from tests.test_analysis import (
        _cleanup_person,
        _create_user,
        _ensure_analysis_code,
        _login,
        _seed_person_with_ready_doc,
    )

    _ensure_analysis_code(db_session, "JOB-DEV-GEN", "JOB", "개발자")
    _ensure_analysis_code(db_session, "JOB-MGT-PM", "JOB", "PM")
    _ensure_analysis_code(db_session, "EXP-AI-RAG", "EXP", "RAG")

    admin = _create_user(
        db_session, login_id=f"ug_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)

    created = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(person.id),
            "document_ids": [str(document.id)],
            "analysis_type": "PROFILE",
        },
    )
    assert created.status_code in {200, 202}, created.text
    analysis_id = created.json()["data"]["analysis_id"]

    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == uuid.UUID(analysis_id))
    ).scalar_one()
    run.status = "REVIEWING"
    db_session.add(run)

    job = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={"code": "JOB-DEV-GEN", "source_refs": []},
        review_status="PENDING",
    )
    exp = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EXP",
        candidate_path="expertise[0]",
        change_type="NEW",
        new_value={"code": "EXP-AI-RAG"},
        review_status="PENDING",
    )
    grounded = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[1]",
        change_type="NEW",
        new_value={
            "code": "JOB-MGT-PM",
            "job_type": "SECONDARY",
            "source_refs": [
                {
                    "document_id": str(document.id),
                    "page_no": 1,
                    "quote_text": "PM 역할",
                }
            ],
        },
        review_status="PENDING",
    )
    project = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROJECT",
        candidate_path="projects[0]",
        change_type="NEW",
        new_value={"project_name": "사업A", "source_refs": []},
        review_status="PENDING",
    )
    modified = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EMPLOYMENT",
        candidate_path="employment_history[0]",
        change_type="NEW",
        new_value={"company_name": "A", "end_date": "2014.09.30"},
        review_status="MODIFIED",
        decided_value={"company_name": "A", "end_date": "2014.09.30"},
    )
    db_session.add_all([job, exp, grounded, project, modified])
    db_session.commit()
    for row in (job, exp, grounded, project, modified):
        db_session.refresh(row)

    bad_job = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(job.id)], "review_status": "ACCEPTED"},
    )
    assert bad_job.status_code == 400, bad_job.text
    assert "원문 근거" in bad_job.text

    bad_exp = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(exp.id)], "review_status": "ACCEPTED"},
    )
    assert bad_exp.status_code == 400, bad_exp.text
    assert "원문 근거" in bad_exp.text

    mixed = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(grounded.id), str(exp.id)],
            "review_status": "ACCEPTED",
        },
    )
    assert mixed.status_code == 400, mixed.text
    assert "원문 근거" in mixed.text
    db_session.refresh(grounded)
    db_session.refresh(exp)
    assert grounded.review_status == "PENDING"
    assert exp.review_status == "PENDING"

    ok_grounded = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(grounded.id)], "review_status": "ACCEPTED"},
    )
    assert ok_grounded.status_code == 200, ok_grounded.text
    db_session.refresh(grounded)
    assert grounded.review_status == "ACCEPTED"

    ok_project = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(project.id)], "review_status": "ACCEPTED"},
    )
    assert ok_project.status_code == 200, ok_project.text

    reject_ungrounded = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={
            "diff_ids": [str(job.id), str(exp.id)],
            "review_status": "REJECTED",
        },
    )
    assert reject_ungrounded.status_code == 200, reject_ungrounded.text
    db_session.refresh(job)
    db_session.refresh(exp)
    assert job.review_status == "REJECTED"
    assert exp.review_status == "REJECTED"

    db_session.refresh(modified)
    assert modified.review_status == "MODIFIED"
    assert modified.decided_value == {
        "company_name": "A",
        "end_date": "2014.09.30",
    }

    # Single ACCEPTED for ungrounded JOB remains allowed (FE warns).
    reset = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{job.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "PENDING"},
    )
    assert reset.status_code == 200, reset.text
    accept_single = client.patch(
        f"/api/v1/analyses/{analysis_id}/diffs/{job.id}",
        headers={"X-CSRF-Token": csrf},
        json={"review_status": "ACCEPTED"},
    )
    assert accept_single.status_code == 200, accept_single.text

    # Linked Evidence (no source_refs quote) still grounds bulk ACCEPTED.
    from app.db.models.analysis import AnalysisDiffEvidence
    from app.db.models.evidence import Evidence

    linked = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EXP",
        candidate_path="expertise[1]",
        change_type="NEW",
        new_value={"code": "EXP-AI-RAG", "source_refs": []},
        review_status="PENDING",
    )
    db_session.add(linked)
    db_session.flush()
    ev = Evidence(
        document_id=document.id,
        page_no=1,
        quote_text="RAG 경험",
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(
        AnalysisDiffEvidence(diff_item_id=linked.id, evidence_id=ev.id)
    )
    db_session.commit()
    db_session.refresh(linked)

    ok_linked = client.post(
        f"/api/v1/analyses/{analysis_id}/diffs/bulk",
        headers={"X-CSRF-Token": csrf},
        json={"diff_ids": [str(linked.id)], "review_status": "ACCEPTED"},
    )
    assert ok_linked.status_code == 200, ok_linked.text

    _cleanup_person(db_session, person.id, admin.id)
