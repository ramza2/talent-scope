"""REVIEWING ROOT JOB/EXP evidence recover Preview/Apply (no Diff regenerate)."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _ensure_code(db_session, code: str, *, code_type: str, name: str) -> None:
    from app.db.models.code import CodeMaster

    if db_session.get(CodeMaster, code) is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type=code_type,
                name=name,
                sort_order=1,
                is_active=True,
            )
        )
        db_session.commit()


def _create_admin(db_session):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=f"recover_{uuid.uuid4().hex[:8]}",
        password_hash=hash_password("Passw0rd!"),
        name="Recover Admin",
        role="ADMIN",
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _skt_page() -> str:
    # Collapsed AI dates 2005.12~2005.12; source period is 2004.04~2005.12.
    return (
        "프로젝트 경력\n"
        "SKT 차세대 빌링\n"
        "2004.04~2005.12\n"
        "SK텔레콤\n"
        "담당업무\n"
        "DA 컨설팅\n"
        "데이터 모델링\n"
        "기타사업\n"
        "2020.01~2020.12\n"
        "고객B\n"
        "회의 참석\n"
    )


def _seed_reviewing_recover_case(db_session, admin_id):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SourceRef,
        SummaryCandidate,
    )
    from app.db.models.analysis import (
        AnalysisDiffItem,
        AnalysisRun,
        AnalysisRunDocument,
    )
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    _ensure_code(db_session, "DOC-PROFILE", code_type="DOC_TYPE", name="프로필")
    _ensure_code(db_session, "JOB-DATA-DA", code_type="JOB", name="DA")
    _ensure_code(db_session, "JOB-DEV-GEN", code_type="JOB", name="개발자")
    _ensure_code(db_session, "EXP-DATA", code_type="EXP", name="Data/DB")
    _ensure_code(db_session, "EXP-DATA-DB-TUNING", code_type="EXP", name="DB 튜닝")

    person = Person(status="ACTIVE", created_by=admin_id)
    db_session.add(person)
    db_session.flush()
    profile = PersonProfile(
        person_id=person.id,
        name="복구대상",
        technical_grade="ADVANCED",
        profile_version=1,
    )
    db_session.add(profile)
    db_session.flush()
    snap = build_confirmed_profile_snapshot(db_session, person.id)
    db_session.add(
        ProfileRevision(
            person_id=person.id,
            revision_no=1,
            snapshot_json=snap,
            source_type="USER",
            created_by=admin_id,
        )
    )

    doc_uuid = uuid.uuid4()
    group = DocumentGroup(
        person_id=person.id,
        document_type_code="DOC-PROFILE",
        title="career.docx",
    )
    db_session.add(group)
    db_session.flush()
    document = Document(
        id=doc_uuid,
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="career.docx",
        extension="docx",
        mime_type="application/octet-stream",
        file_size=100,
        storage_key=f"test/{doc_uuid}.bin",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        processing_status="READY",
        uploaded_by=admin_id,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        DocumentPage(
            document_id=document.id,
            page_no=1,
            extracted_text=_skt_page(),
            layout_json={"needs_vlm": False},
            extraction_method="TEXT_PARSER",
        )
    )

    doc_id = str(doc_uuid)
    skt = ProjectCandidate(
        project_name="SKT 차세대 빌링",
        customer_name="SK텔레콤",
        start_date="2005.12",
        end_date="2005.12",
        duration_months=0,
        jobs=[
            CodeRefCandidate(
                code="JOB-DATA-DA",
                source_refs=[
                    SourceRef(
                        document_id=doc_id, page_no=1, quote_text="DA 컨설팅"
                    )
                ],
            )
        ],
        expertise=[
            CodeRefCandidate(
                code="EXP-DATA",
                source_refs=[
                    SourceRef(
                        document_id=doc_id,
                        page_no=1,
                        quote_text="데이터 모델링",
                    )
                ],
            )
        ],
        source_refs=[
            SourceRef(
                document_id=doc_id, page_no=1, quote_text="DA 컨설팅"
            )
        ],
    )
    others = [
        ProjectCandidate(
            project_name=f"기타사업{i}",
            customer_name="고객B",
            start_date="2020.01",
            end_date="2020.12",
            source_refs=[
                SourceRef(
                    document_id=doc_id, page_no=1, quote_text="회의 참석"
                )
            ],
        )
        for i in range(69)
    ]
    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        analysis=AnalysisMetaCandidate(),
        profile=ProfileCandidate(name="복구대상"),
        jobs=[
            JobCandidate(code="JOB-DATA-DA", raw_value="JOB-DATA-DA"),
            JobCandidate(code="JOB-DEV-GEN", raw_value="JOB-DEV-GEN"),
        ],
        expertise=[
            ExpertiseCandidate(code="EXP-DATA", raw_value="EXP-DATA"),
            ExpertiseCandidate(
                code="EXP-DATA-DB-TUNING", raw_value="EXP-DATA-DB-TUNING"
            ),
        ],
        projects=[skt, *others],
        summary=SummaryCandidate(),
    )
    assert len(candidate.projects) == 70

    run = AnalysisRun(
        person_id=person.id,
        status="REVIEWING",
        base_profile_version=1,
        candidate_json=candidate.to_storage_dict(),
        prompt_version="test",
        schema_version="test",
        overall_confidence=Decimal("0.9000"),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        AnalysisRunDocument(
            analysis_run_id=run.id,
            document_id=document.id,
            analysis_role="SOURCE",
        )
    )

    # PENDING JOB/EXP diffs for supplemented and non-supplemented codes.
    pending_job = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[0]",
        change_type="NEW",
        new_value={
            "code": "JOB-DATA-DA",
            "raw_value": "JOB-DATA-DA",
            "job_type": "PRIMARY",
            "source_refs": [],
        },
        review_status="PENDING",
    )
    pending_exp = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="EXP",
        candidate_path="expertise[0]",
        change_type="NEW",
        new_value={
            "code": "EXP-DATA",
            "raw_value": "EXP-DATA",
            "source_refs": [],
        },
        review_status="PENDING",
    )
    pending_ungrounded = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="JOB",
        candidate_path="jobs[1]",
        change_type="NEW",
        new_value={
            "code": "JOB-DEV-GEN",
            "raw_value": "JOB-DEV-GEN",
            "job_type": "PRIMARY",
            "source_refs": [],
        },
        review_status="PENDING",
    )
    # User-modified project diff must survive unchanged.
    modified_project = AnalysisDiffItem(
        analysis_run_id=run.id,
        entity_type="PROJECT",
        candidate_path="projects[0]",
        change_type="UPDATE",
        new_value={
            "project_name": "SKT 차세대 빌링",
            "start_date": "2005.12",
            "end_date": "2005.12",
            "customer_name": "SK텔레콤",
        },
        review_status="MODIFIED",
        decided_value={
            "project_name": "SKT 차세대 빌링(수정)",
            "start_date": "2005.12",
            "end_date": "2005.12",
        },
    )
    db_session.add_all(
        [pending_job, pending_exp, pending_ungrounded, modified_project]
    )
    db_session.commit()
    db_session.refresh(run)
    db_session.refresh(pending_job)
    db_session.refresh(pending_exp)
    db_session.refresh(pending_ungrounded)
    db_session.refresh(modified_project)
    return {
        "run": run,
        "person": person,
        "pending_job_id": pending_job.id,
        "pending_exp_id": pending_exp.id,
        "pending_ungrounded_id": pending_ungrounded.id,
        "modified_project_id": modified_project.id,
        "admin_id": admin_id,
    }


def test_preview_lists_targets_without_db_write(db_session) -> None:
    from app.modules.analysis.root_evidence_recover import (
        preview_root_evidence_recover,
    )

    admin = _create_admin(db_session)
    seeded = _seed_reviewing_recover_case(db_session, admin.id)
    run_id = seeded["run"].id
    before_json = dict(seeded["run"].candidate_json)

    result = preview_root_evidence_recover(db_session, run_id)
    assert result.changed is True
    assert result.applied is False
    assert "JOB-DATA-DA" in result.supplemented_jobs
    assert "EXP-DATA" in result.supplemented_expertise
    assert "JOB-DEV-GEN" not in result.supplemented_jobs
    assert "EXP-DATA-DB-TUNING" not in result.supplemented_expertise
    assert result.pending_diffs_updated == 0
    assert result.project_dates_unchanged is True

    db_session.expire_all()
    from app.db.models.analysis import AnalysisRun

    run = db_session.get(AnalysisRun, run_id)
    assert run is not None
    assert run.candidate_json == before_json


def test_apply_recovers_refs_preserves_projects_and_modified(db_session) -> None:
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
    from app.modules.analysis.root_evidence_recover import (
        apply_root_evidence_recover,
        preview_root_evidence_recover,
    )

    admin = _create_admin(db_session)
    seeded = _seed_reviewing_recover_case(db_session, admin.id)
    run_id = seeded["run"].id

    preview = preview_root_evidence_recover(db_session, run_id)
    assert preview.changed is True

    result = apply_root_evidence_recover(
        db_session, run_id, actor_user_id=admin.id
    )
    assert result.applied is True
    assert result.changed is True
    assert set(result.supplemented_jobs) == {"JOB-DATA-DA"}
    assert set(result.supplemented_expertise) == {"EXP-DATA"}
    assert result.pending_diffs_updated >= 2
    assert result.project_dates_unchanged is True

    db_session.expire_all()
    run = db_session.get(AnalysisRun, run_id)
    assert run is not None
    jobs = run.candidate_json["jobs"]
    exps = run.candidate_json["expertise"]
    da = next(j for j in jobs if j["code"] == "JOB-DATA-DA")
    assert any((r.get("quote_text") or "").strip() == "DA 컨설팅" for r in da["source_refs"])
    exp = next(x for x in exps if x["code"] == "EXP-DATA")
    assert any(
        (r.get("quote_text") or "").strip() == "데이터 모델링"
        for r in exp["source_refs"]
    )
    gen = next(j for j in jobs if j["code"] == "JOB-DEV-GEN")
    assert not any((r.get("quote_text") or "").strip() for r in (gen.get("source_refs") or []))
    tuning = next(x for x in exps if x["code"] == "EXP-DATA-DB-TUNING")
    assert not any(
        (r.get("quote_text") or "").strip() for r in (tuning.get("source_refs") or [])
    )

    projects = run.candidate_json["projects"]
    assert len(projects) == 70
    skt = projects[0]
    assert skt["start_date"] == "2005.12"
    assert skt["end_date"] == "2005.12"
    assert projects[1]["project_name"] == "기타사업0"
    assert projects[1]["start_date"] == "2020.01"

    modified = db_session.get(AnalysisDiffItem, seeded["modified_project_id"])
    assert modified is not None
    assert modified.review_status == "MODIFIED"
    assert modified.decided_value == {
        "project_name": "SKT 차세대 빌링(수정)",
        "start_date": "2005.12",
        "end_date": "2005.12",
    }
    assert modified.id == seeded["modified_project_id"]

    pending_job = db_session.get(AnalysisDiffItem, seeded["pending_job_id"])
    assert pending_job is not None
    assert pending_job.review_status == "PENDING"
    assert pending_job.id == seeded["pending_job_id"]
    assert any(
        (r.get("quote_text") or "") == "DA 컨설팅"
        for r in (pending_job.new_value or {}).get("source_refs") or []
    )

    pending_ungrounded = db_session.get(
        AnalysisDiffItem, seeded["pending_ungrounded_id"]
    )
    assert pending_ungrounded is not None
    assert not any(
        (r.get("quote_text") or "").strip()
        for r in (pending_ungrounded.new_value or {}).get("source_refs") or []
    )


def test_apply_is_idempotent(db_session) -> None:
    from app.modules.analysis.root_evidence_recover import apply_root_evidence_recover

    admin = _create_admin(db_session)
    seeded = _seed_reviewing_recover_case(db_session, admin.id)
    run_id = seeded["run"].id

    first = apply_root_evidence_recover(
        db_session, run_id, actor_user_id=admin.id
    )
    assert first.applied is True
    second = apply_root_evidence_recover(
        db_session, run_id, actor_user_id=admin.id
    )
    assert second.changed is False
    assert second.applied is False
    assert second.supplemented_jobs == []


def test_apply_rolls_back_on_failure(db_session, monkeypatch) -> None:
    from app.db.models.analysis import AnalysisRun
    from app.modules.analysis import root_evidence_recover as mod

    admin = _create_admin(db_session)
    seeded = _seed_reviewing_recover_case(db_session, admin.id)
    run_id = seeded["run"].id
    before = dict(seeded["run"].candidate_json)

    def _boom(*_a, **_k):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(mod, "_patch_pending_root_diffs", _boom)
    with pytest.raises(RuntimeError, match="forced failure"):
        mod.apply_root_evidence_recover(
            db_session, run_id, actor_user_id=admin.id
        )

    db_session.expire_all()
    run = db_session.get(AnalysisRun, run_id)
    assert run is not None
    assert run.candidate_json == before
    # Diff IDs and MODIFIED decided_value untouched.
    from app.db.models.analysis import AnalysisDiffItem

    modified = db_session.get(AnalysisDiffItem, seeded["modified_project_id"])
    assert modified is not None
    assert modified.review_status == "MODIFIED"
    assert modified.decided_value["project_name"] == "SKT 차세대 빌링(수정)"
