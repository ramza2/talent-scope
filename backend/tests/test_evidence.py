"""Tests for TalentScope Confirm-time Evidence Materialization."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(redis_prefix: str, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.run_profile_analysis.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.analysis_tasks.identify_upload_session.delay",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "app.tasks.document_tasks.process_document.delay",
        lambda *_a, **_k: None,
    )

    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app
    from app.storage.s3 import reset_object_storage_cache

    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    from app.storage.s3 import build_object_storage

    storage = build_object_storage()
    if hasattr(storage, "clear"):
        storage.clear()
    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()
    reset_object_storage_cache()


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --------------------------------------------------------------------------- helpers


def _create_user(db_session, *, login_id: str, password: str, role: str = "ADMIN"):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id,
        password_hash=hash_password(password),
        name="Tester",
        role=role,
        status="ACTIVE",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _login(client: TestClient, login_id: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})
    assert resp.status_code == 200, resp.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


def _ensure_doc_type(db_session) -> str:
    from app.db.models.code import CodeMaster

    code = "DOC-RESUME"
    row = db_session.get(CodeMaster, code)
    if row is None:
        db_session.add(
            CodeMaster(
                code=code,
                code_type="DOC_TYPE",
                name="이력서",
                sort_order=1,
                is_active=True,
            )
        )
        db_session.commit()
    return code


def _ensure_code(db_session, code: str, code_type: str, name: str) -> None:
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


def _ensure_common_codes(db_session) -> None:
    _ensure_code(db_session, "TECH-LANG-PYTHON", "TECH", "Python")
    _ensure_code(db_session, "JOB-AI-DEV", "JOB", "AI 개발")
    _ensure_code(db_session, "EXP-AI-RAG", "EXP", "RAG")
    _ensure_code(db_session, "BIZ-PUBLIC", "BIZ", "공공")


def _seed_person_with_ready_doc(db_session, user_id, *, page_text: str | None = None):
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    doc_type = _ensure_doc_type(db_session)
    person = Person(status="ACTIVE", created_by=user_id)
    db_session.add(person)
    db_session.flush()
    profile = PersonProfile(
        person_id=person.id,
        name="분석대상",
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
            created_by=user_id,
        )
    )
    group = DocumentGroup(
        person_id=person.id,
        document_type_code=doc_type,
        title="이력서",
    )
    db_session.add(group)
    db_session.flush()
    document = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="resume.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256="a" * 64,
        processing_status="READY",
        uploaded_by=user_id,
    )
    db_session.add(document)
    db_session.flush()
    text = page_text or "홍길동 / Python / AI 개발자 / 기술등급 특급"
    db_session.add(
        DocumentPage(
            document_id=document.id,
            page_no=1,
            extracted_text=text,
            layout_json={"needs_vlm": False},
            extraction_method="TEXT_PARSER",
        )
    )
    db_session.commit()
    return person, document


def _cleanup_person(db_session, person_id, user_id) -> None:
    from app.db.models.analysis import (
        AnalysisDiffEvidence,
        AnalysisDiffItem,
        AnalysisRun,
        AnalysisRunDocument,
    )
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.evidence import Evidence, EvidenceLink
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexJob
    from app.db.models.user import AppUser

    run_ids = list(
        db_session.execute(
            select(AnalysisRun.id).where(AnalysisRun.person_id == person_id)
        ).scalars().all()
    )
    if run_ids:
        diff_ids = list(
            db_session.execute(
                select(AnalysisDiffItem.id).where(
                    AnalysisDiffItem.analysis_run_id.in_(run_ids)
                )
            ).scalars().all()
        )
        if diff_ids:
            db_session.execute(
                delete(AnalysisDiffEvidence).where(
                    AnalysisDiffEvidence.diff_item_id.in_(diff_ids)
                )
            )
        db_session.execute(
            delete(AnalysisDiffItem).where(AnalysisDiffItem.analysis_run_id.in_(run_ids))
        )
        db_session.execute(
            delete(AnalysisRunDocument).where(
                AnalysisRunDocument.analysis_run_id.in_(run_ids)
            )
        )
        db_session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))

    group_ids = list(
        db_session.execute(
            select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)
        ).scalars().all()
    )
    if group_ids:
        doc_ids = list(
            db_session.execute(
                select(Document.id).where(Document.document_group_id.in_(group_ids))
            ).scalars().all()
        )
        if doc_ids:
            ev_ids = list(
                db_session.execute(
                    select(Evidence.id).where(Evidence.document_id.in_(doc_ids))
                ).scalars().all()
            )
            if ev_ids:
                db_session.execute(
                    delete(EvidenceLink).where(EvidenceLink.evidence_id.in_(ev_ids))
                )
                db_session.execute(
                    delete(AnalysisDiffEvidence).where(
                        AnalysisDiffEvidence.evidence_id.in_(ev_ids)
                    )
                )
                db_session.execute(delete(Evidence).where(Evidence.id.in_(ev_ids)))
            db_session.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(doc_ids)))
            db_session.execute(delete(Document).where(Document.id.in_(doc_ids)))
        db_session.execute(delete(DocumentGroup).where(DocumentGroup.id.in_(group_ids)))

    db_session.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db_session.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db_session.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db_session.execute(delete(Person).where(Person.id == person_id))
    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _source_ref(document, *, page_no: int = 1, quote_text: str) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "page_no": page_no,
        "quote_text": quote_text,
    }


def _set_page(
    db_session,
    document,
    *,
    extracted_text: str,
    needs_vlm: bool = False,
    extraction_method: str = "TEXT_PARSER",
    page_no: int = 1,
) -> None:
    from app.db.models.document import DocumentPage

    page = db_session.execute(
        select(DocumentPage).where(
            DocumentPage.document_id == document.id,
            DocumentPage.page_no == page_no,
        )
    ).scalar_one()
    page.extracted_text = extracted_text
    page.layout_json = {"needs_vlm": needs_vlm}
    page.extraction_method = extraction_method
    db_session.add(page)
    db_session.commit()


def _seed_reviewing_run(db_session, *, person, document, diffs: list[dict[str, Any]]):
    """Lightweight AnalysisRun + AnalysisRunDocument + AnalysisDiffItem setup."""
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument

    analysis = AnalysisRun(
        person_id=person.id,
        status="REVIEWING",
        base_profile_version=1,
    )
    db_session.add(analysis)
    db_session.flush()
    db_session.add(
        AnalysisRunDocument(analysis_run_id=analysis.id, document_id=document.id)
    )
    created = []
    for spec in diffs:
        row = AnalysisDiffItem(analysis_run_id=analysis.id, **spec)
        db_session.add(row)
        created.append(row)
    db_session.commit()
    for row in created:
        db_session.refresh(row)
    db_session.refresh(analysis)
    return analysis, created


def _confirm(client: TestClient, csrf: str, analysis_id, *, version: int = 1):
    return client.post(
        f"/api/v1/analyses/{analysis_id}/confirm",
        headers={"X-CSRF-Token": csrf},
        json={"expected_profile_version": version},
    )


def _evidence_counts(db_session, *, document_id=None) -> dict[str, int]:
    from app.db.models.analysis import AnalysisDiffEvidence
    from app.db.models.evidence import Evidence, EvidenceLink

    ev_q = select(func.count()).select_from(Evidence)
    if document_id is not None:
        ev_q = ev_q.where(Evidence.document_id == document_id)
    evidence_n = db_session.execute(ev_q).scalar_one()

    link_q = select(func.count()).select_from(EvidenceLink)
    if document_id is not None:
        link_q = link_q.join(Evidence, Evidence.id == EvidenceLink.evidence_id).where(
            Evidence.document_id == document_id
        )
    link_n = db_session.execute(link_q).scalar_one()

    diff_q = select(func.count()).select_from(AnalysisDiffEvidence)
    if document_id is not None:
        diff_q = diff_q.join(
            Evidence, Evidence.id == AnalysisDiffEvidence.evidence_id
        ).where(Evidence.document_id == document_id)
    diff_n = db_session.execute(diff_q).scalar_one()

    return {
        "evidence": int(evidence_n),
        "links": int(link_n),
        "diff_evidence": int(diff_n),
    }


def _list_evidence_for_doc(db_session, document_id):
    from app.db.models.evidence import Evidence

    return list(
        db_session.execute(
            select(Evidence).where(Evidence.document_id == document_id)
        ).scalars().all()
    )


def _links_for_evidence(db_session, evidence_id):
    from app.db.models.evidence import EvidenceLink

    return list(
        db_session.execute(
            select(EvidenceLink).where(EvidenceLink.evidence_id == evidence_id)
        ).scalars().all()
    )


def _diff_evidence_for(db_session, diff_id):
    from app.db.models.analysis import AnalysisDiffEvidence

    return list(
        db_session.execute(
            select(AnalysisDiffEvidence).where(
                AnalysisDiffEvidence.diff_item_id == diff_id
            )
        ).scalars().all()
    )


# --------------------------------------------------------------------------- unit


def test_extract_source_refs_unit():
    from app.modules.evidence.materializer import extract_source_refs

    assert extract_source_refs(None) == []
    assert extract_source_refs("plain") == []
    assert extract_source_refs({"code": "TECH-LANG-PYTHON"}) == []
    assert extract_source_refs({"source_refs": "bad"}) == []
    assert extract_source_refs({"source_refs": [1, "x", None]}) == []

    refs = [
        {"document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "page_no": 1, "quote_text": "a"},
        "skip-me",
        {"document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "page_no": 2, "quote_text": "b"},
    ]
    out = extract_source_refs({"value": "X", "source_refs": refs})
    assert out == [refs[0], refs[2]]

    # decided_value-shaped payload is irrelevant — helper only reads the given new_value
    assert extract_source_refs({"source_refs": [{"quote_text": "only"}]}) == [
        {"quote_text": "only"}
    ]


def test_should_create_evidence_link_and_project_field_name_unit():
    from types import SimpleNamespace

    from app.modules.evidence.materializer import (
        project_relation_field_name,
        should_create_evidence_link,
    )

    assert project_relation_field_name("skills", "TECH-LANG-PYTHON") == (
        "skills:TECH-LANG-PYTHON"
    )
    assert should_create_evidence_link(
        SimpleNamespace(change_type="SAME", review_status="SAME")
    )
    assert should_create_evidence_link(
        SimpleNamespace(change_type="NEW", review_status="ACCEPTED")
    )
    assert should_create_evidence_link(
        SimpleNamespace(change_type="REVIEW", review_status="MERGED")
    )
    assert not should_create_evidence_link(
        SimpleNamespace(change_type="UPDATE", review_status="MODIFIED")
    )
    assert not should_create_evidence_link(
        SimpleNamespace(change_type="NEW", review_status="REJECTED")
    )


# --------------------------------------------------------------------------- confirm materialization


def test_profile_accepted_creates_evidence_and_person_profile_link(
    client: TestClient, db_session
):
    quote = "이름: 김증거"
    admin = _create_user(
        db_session, login_id=f"ev_p_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"머리말 {quote} 꼬리말"
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.name",
                "field_name": "name",
                "change_type": "UPDATE",
                "old_value": "분석대상",
                "new_value": {
                    "value": "김증거",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    counts = _evidence_counts(db_session, document_id=document.id)
    assert counts == {"evidence": 1, "links": 1, "diff_evidence": 1}

    ev = _list_evidence_for_doc(db_session, document.id)[0]
    assert ev.quote_text == quote
    assert ev.char_start == f"머리말 {quote} 꼬리말".index(quote)
    assert ev.char_end == ev.char_start + len(quote)
    assert ev.extraction_method == "TEXT_PARSER"
    assert ev.page_no == 1

    links = _links_for_evidence(db_session, ev.id)
    assert len(links) == 1
    assert links[0].target_type == "PERSON_PROFILE"
    assert links[0].target_id == person.id
    assert links[0].field_name == "name"
    assert links[0].relation_type == "SUPPORTS"

    de = _diff_evidence_for(db_session, diffs[0].id)
    assert len(de) == 1
    assert de[0].evidence_id == ev.id

    _cleanup_person(db_session, person.id, admin.id)


def test_same_tech_creates_person_skill_link_without_mutating_metadata(
    client: TestClient, db_session
):
    from app.db.models.person import PersonSkill

    _ensure_common_codes(db_session)
    quote = "Python 숙련"
    admin = _create_user(
        db_session, login_id=f"ev_s_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"스킬 섹션 {quote} 끝"
    )
    skill = PersonSkill(
        person_id=person.id,
        tech_code="TECH-LANG-PYTHON",
        last_used_year=2024,
        experience_months=60,
        is_representative=True,
        source_type="USER",
        confirmed_at=datetime.now(UTC),
    )
    db_session.add(skill)
    db_session.commit()
    db_session.refresh(skill)

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "TECH",
                "candidate_path": "skills[0]",
                "change_type": "SAME",
                "old_value": {
                    "code": "TECH-LANG-PYTHON",
                    "last_used_year": 2024,
                    "experience_months": 60,
                    "is_representative": True,
                },
                "new_value": {
                    "code": "TECH-LANG-PYTHON",
                    "last_used_year": 2099,
                    "experience_months": 1,
                    "is_representative": False,
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                # change_type=SAME; review_status must be PENDING (DB check constraint)
                "review_status": "PENDING",
                "existing_target_id": skill.id,
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    skill2 = db_session.execute(
        select(PersonSkill).where(PersonSkill.id == skill.id)
    ).scalar_one()
    assert skill2.last_used_year == 2024
    assert skill2.experience_months == 60
    assert skill2.is_representative is True
    assert skill2.source_type == "USER"

    counts = _evidence_counts(db_session, document_id=document.id)
    assert counts == {"evidence": 1, "links": 1, "diff_evidence": 1}
    ev = _list_evidence_for_doc(db_session, document.id)[0]
    links = _links_for_evidence(db_session, ev.id)
    assert links[0].target_type == "PERSON_SKILL"
    assert links[0].target_id == skill.id
    assert links[0].field_name == "tech_code"
    assert _diff_evidence_for(db_session, diffs[0].id)[0].evidence_id == ev.id

    _cleanup_person(db_session, person.id, admin.id)


def test_rejected_creates_evidence_without_link(client: TestClient, db_session):
    quote = "거절된 근거 문구"
    admin = _create_user(
        db_session, login_id=f"ev_r_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {quote}"
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.phone",
                "field_name": "phone",
                "change_type": "NEW",
                "new_value": {
                    "value": "010-0000-0000",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "REJECTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    counts = _evidence_counts(db_session, document_id=document.id)
    assert counts["evidence"] == 1
    assert counts["diff_evidence"] == 1
    assert counts["links"] == 0
    assert _diff_evidence_for(db_session, diffs[0].id)
    assert _links_for_evidence(db_session, _list_evidence_for_doc(db_session, document.id)[0].id) == []

    _cleanup_person(db_session, person.id, admin.id)


def test_modified_technical_grade_no_link_ignores_decided_value_refs(
    client: TestClient, db_session
):
    from app.db.models.person import PersonProfile

    real_quote = "특급 기술등급"
    fake_quote = "가짜 decided 근거"
    admin = _create_user(
        db_session, login_id=f"ev_m_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session,
        admin.id,
        page_text=f"등급 설명 {real_quote} / {fake_quote}",
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.technical_grade",
                "field_name": "technical_grade",
                "change_type": "UPDATE",
                "old_value": "ADVANCED",
                "new_value": {
                    "value": "EXPERT",
                    "source_refs": [_source_ref(document, quote_text=real_quote)],
                },
                "review_status": "MODIFIED",
                "decided_value": {
                    "value": "INTERMEDIATE",
                    "source_refs": [
                        _source_ref(document, quote_text=fake_quote),
                        {
                            "document_id": str(uuid.uuid4()),
                            "page_no": 99,
                            "quote_text": "절대 무시",
                        },
                    ],
                },
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.technical_grade == "INTERMEDIATE"

    evidences = _list_evidence_for_doc(db_session, document.id)
    assert len(evidences) == 1
    assert evidences[0].quote_text == real_quote
    assert _evidence_counts(db_session, document_id=document.id)["links"] == 0
    assert _diff_evidence_for(db_session, diffs[0].id)
    assert all(e.quote_text != fake_quote for e in evidences)

    _cleanup_person(db_session, person.id, admin.id)


def test_merged_project_link_field_name_null(client: TestClient, db_session):
    from app.db.models.project import Project

    _ensure_common_codes(db_session)
    quote = "프로젝트 병합 근거"
    admin = _create_user(
        db_session, login_id=f"ev_mg_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"경력 {quote}"
    )
    project = Project(
        person_id=person.id,
        project_name="기존 프로젝트",
        source_type="USER",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROJECT",
                "candidate_path": "projects[0]",
                "field_name": None,
                "change_type": "REVIEW",
                "existing_target_id": project.id,
                "new_value": {
                    "project_name": "기존 프로젝트",
                    "project_summary": "보강 요약",
                    "business_domains": [{"code": "BIZ-PUBLIC"}],
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "MERGED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    ev = _list_evidence_for_doc(db_session, document.id)[0]
    links = _links_for_evidence(db_session, ev.id)
    assert len(links) == 1
    assert links[0].target_type == "PROJECT"
    assert links[0].target_id == project.id
    assert links[0].field_name is None
    assert _diff_evidence_for(db_session, diffs[0].id)

    _cleanup_person(db_session, person.id, admin.id)


def test_project_relation_child_update_link_field_name(client: TestClient, db_session):
    from app.db.models.project import Project, ProjectSkill

    _ensure_common_codes(db_session)
    quote = "프로젝트 Python 사용"
    admin = _create_user(
        db_session, login_id=f"ev_pr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"상세 {quote}"
    )
    project = Project(
        person_id=person.id,
        project_name="관계 프로젝트",
        source_type="USER",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROJECT",
                "candidate_path": "projects[0].skills",
                "field_name": "skills",
                "change_type": "UPDATE",
                "existing_target_id": project.id,
                "new_value": {
                    "code": "TECH-LANG-PYTHON",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    assert db_session.execute(
        select(ProjectSkill).where(
            ProjectSkill.project_id == project.id,
            ProjectSkill.tech_code == "TECH-LANG-PYTHON",
        )
    ).scalar_one_or_none()

    ev = _list_evidence_for_doc(db_session, document.id)[0]
    links = _links_for_evidence(db_session, ev.id)
    assert len(links) == 1
    assert links[0].target_type == "PROJECT"
    assert links[0].target_id == project.id
    assert links[0].field_name == "skills:TECH-LANG-PYTHON"
    assert _diff_evidence_for(db_session, diffs[0].id)

    _cleanup_person(db_session, person.id, admin.id)


def test_new_person_skill_and_employment_link_target_ids(client: TestClient, db_session):
    from app.db.models.person import EmploymentHistory, PersonSkill

    _ensure_common_codes(db_session)
    skill_quote = "신규 Python 스킬"
    emp_quote = "오픈링크 근무"
    admin = _create_user(
        db_session, login_id=f"ev_n_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session,
        admin.id,
        page_text=f"{skill_quote} / {emp_quote}",
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "TECH",
                "candidate_path": "skills[0]",
                "change_type": "NEW",
                "new_value": {
                    "code": "TECH-LANG-PYTHON",
                    "last_used_year": 2025,
                    "source_refs": [_source_ref(document, quote_text=skill_quote)],
                },
                "review_status": "ACCEPTED",
            },
            {
                "entity_type": "EMPLOYMENT",
                "candidate_path": "employment_history[0]",
                "field_name": None,
                "change_type": "NEW",
                "new_value": {
                    "company_name": "오픈링크",
                    "title": "엔지니어",
                    "start_date": "2018-01",
                    "end_date": "2020-12",
                    "source_refs": [_source_ref(document, quote_text=emp_quote)],
                },
                "review_status": "ACCEPTED",
            },
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    skill = db_session.execute(
        select(PersonSkill).where(
            PersonSkill.person_id == person.id,
            PersonSkill.tech_code == "TECH-LANG-PYTHON",
        )
    ).scalar_one()
    emp = db_session.execute(
        select(EmploymentHistory).where(EmploymentHistory.person_id == person.id)
    ).scalar_one()
    assert isinstance(skill.id, uuid.UUID)
    assert isinstance(emp.id, uuid.UUID)

    evidences = {e.quote_text: e for e in _list_evidence_for_doc(db_session, document.id)}
    assert set(evidences) == {skill_quote, emp_quote}

    skill_links = _links_for_evidence(db_session, evidences[skill_quote].id)
    assert skill_links[0].target_type == "PERSON_SKILL"
    assert skill_links[0].target_id == skill.id

    emp_links = _links_for_evidence(db_session, evidences[emp_quote].id)
    assert emp_links[0].target_type == "EMPLOYMENT_HISTORY"
    assert emp_links[0].target_id == emp.id

    assert _diff_evidence_for(db_session, diffs[0].id)
    assert _diff_evidence_for(db_session, diffs[1].id)

    _cleanup_person(db_session, person.id, admin.id)


def test_dedupe_same_quote_two_diffs_one_evidence(client: TestClient, db_session):
    quote = "공유 인용문"
    admin = _create_user(
        db_session, login_id=f"ev_d_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"서두 {quote} 결말"
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.name",
                "field_name": "name",
                "change_type": "UPDATE",
                "old_value": "분석대상",
                "new_value": {
                    "value": "공유이름",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            },
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.phone",
                "field_name": "phone",
                "change_type": "NEW",
                "new_value": {
                    "value": "010-2222-3333",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            },
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    counts = _evidence_counts(db_session, document_id=document.id)
    assert counts["evidence"] == 1
    assert counts["diff_evidence"] == 2
    assert counts["links"] == 2

    ev_id = _list_evidence_for_doc(db_session, document.id)[0].id
    assert _diff_evidence_for(db_session, diffs[0].id)[0].evidence_id == ev_id
    assert _diff_evidence_for(db_session, diffs[1].id)[0].evidence_id == ev_id

    _cleanup_person(db_session, person.id, admin.id)


def test_char_offsets_present_or_null(client: TestClient, db_session):
    present_quote = "오프셋있음"
    missing_quote = "페이지에없는인용"
    admin = _create_user(
        db_session, login_id=f"ev_c_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    page_text = f"AAA {present_quote} BBB"
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=page_text
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.name",
                "field_name": "name",
                "change_type": "UPDATE",
                "old_value": "분석대상",
                "new_value": {
                    "value": "오프셋이름",
                    "source_refs": [
                        _source_ref(document, quote_text=present_quote),
                        _source_ref(document, quote_text=missing_quote),
                    ],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    by_quote = {e.quote_text: e for e in _list_evidence_for_doc(db_session, document.id)}
    assert set(by_quote) == {present_quote, missing_quote}

    hit = by_quote[present_quote]
    assert hit.char_start == page_text.index(present_quote)
    assert hit.char_end == hit.char_start + len(present_quote)
    assert hit.extraction_method == "TEXT_PARSER"

    miss = by_quote[missing_quote]
    assert miss.char_start is None
    assert miss.char_end is None
    assert miss.extraction_method == "TEXT_PARSER"

    _cleanup_person(db_session, person.id, admin.id)


def test_vlm_when_needs_vlm_and_quote_missing(client: TestClient, db_session):
    quote = "비전모델전용인용"
    admin = _create_user(
        db_session, login_id=f"ev_v_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text="OCR 텍스트에는 없는 페이지"
    )
    _set_page(
        db_session,
        document,
        extracted_text="OCR 텍스트에는 없는 페이지",
        needs_vlm=True,
        extraction_method="TEXT_PARSER",
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.profile_summary",
                "field_name": "profile_summary",
                "change_type": "NEW",
                "new_value": {
                    "value": "요약",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    ev = _list_evidence_for_doc(db_session, document.id)[0]
    assert ev.quote_text == quote
    assert ev.extraction_method == "VLM"
    assert ev.char_start is None
    assert ev.char_end is None

    _cleanup_person(db_session, person.id, admin.id)


def test_invalid_refs_skipped_valid_kept_confirm_succeeds(client: TestClient, db_session):
    valid_quote = "유효한 인용"
    admin = _create_user(
        db_session, login_id=f"ev_i_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {valid_quote}"
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.email",
                "field_name": "email",
                "change_type": "NEW",
                "new_value": {
                    "value": "ok@example.com",
                    "source_refs": [
                        {
                            "document_id": "not-a-uuid",
                            "page_no": 1,
                            "quote_text": "bad-doc",
                        },
                        {
                            "document_id": str(uuid.uuid4()),
                            "page_no": 1,
                            "quote_text": "foreign-doc",
                        },
                        {
                            "document_id": str(document.id),
                            "page_no": 0,
                            "quote_text": "bad-page",
                        },
                        {
                            "document_id": str(document.id),
                            "page_no": 99,
                            "quote_text": "missing-page",
                        },
                        {
                            "document_id": str(document.id),
                            "page_no": 1,
                            "quote_text": "   ",
                        },
                        _source_ref(document, quote_text=valid_quote),
                    ],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    evidences = _list_evidence_for_doc(db_session, document.id)
    assert len(evidences) == 1
    assert evidences[0].quote_text == valid_quote
    assert _evidence_counts(db_session, document_id=document.id) == {
        "evidence": 1,
        "links": 1,
        "diff_evidence": 1,
    }
    assert _diff_evidence_for(db_session, diffs[0].id)

    _cleanup_person(db_session, person.id, admin.id)


def test_idempotent_second_confirm_does_not_duplicate_evidence(
    client: TestClient, db_session
):
    quote = "멱등 인용"
    admin = _create_user(
        db_session, login_id=f"ev_id_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"텍스트 {quote}"
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.current_title",
                "field_name": "current_title",
                "change_type": "NEW",
                "new_value": {
                    "value": "시니어",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    first = _confirm(client, csrf, analysis.id)
    assert first.status_code == 200, first.text
    db_session.expire_all()
    before = _evidence_counts(db_session, document_id=document.id)
    assert before["evidence"] == 1

    second = _confirm(client, csrf, analysis.id, version=2)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status"] == "CONFIRMED"

    db_session.expire_all()
    after = _evidence_counts(db_session, document_id=document.id)
    assert after == before

    _cleanup_person(db_session, person.id, admin.id)


def test_get_and_list_evidence_with_auth(client: TestClient, db_session):
    quote = "API 조회 인용"
    admin = _create_user(
        db_session, login_id=f"ev_a_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {quote}"
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.department",
                "field_name": "department",
                "change_type": "NEW",
                "new_value": {
                    "value": "R&D",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )
    assert _confirm(client, csrf, analysis.id).status_code == 200

    db_session.expire_all()
    ev = _list_evidence_for_doc(db_session, document.id)[0]

    client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})

    unauth_detail = client.get(f"/api/v1/evidence/{ev.id}")
    assert unauth_detail.status_code == 401
    unauth_list = client.get(
        "/api/v1/evidence",
        params={
            "target_type": "PERSON_PROFILE",
            "target_id": str(person.id),
            "field_name": "department",
        },
    )
    assert unauth_list.status_code == 401

    csrf = _login(client, admin.login_id, "Passw0rd!")

    detail = client.get(f"/api/v1/evidence/{ev.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]
    assert body["id"] == str(ev.id)
    assert body["quote_text"] == quote
    assert body["page_no"] == 1
    assert body["char_start"] is not None
    assert body["extraction_method"] == "TEXT_PARSER"
    assert body["document"]["id"] == str(document.id)
    assert any(
        link["target_type"] == "PERSON_PROFILE"
        and link["field_name"] == "department"
        and link["target_id"] == str(person.id)
        for link in body["links"]
    )

    listed = client.get(
        "/api/v1/evidence",
        params={
            "target_type": "PERSON_PROFILE",
            "target_id": str(person.id),
            "field_name": "department",
        },
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == str(ev.id)
    assert rows[0]["quote_text"] == quote

    empty = client.get(
        "/api/v1/evidence",
        params={
            "target_type": "PERSON_PROFILE",
            "target_id": str(person.id),
            "field_name": "phone",
        },
    )
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    _cleanup_person(db_session, person.id, admin.id)


def test_diff_get_after_confirm_includes_evidence_ids(client: TestClient, db_session):
    quote = "Diff 응답 근거"
    admin = _create_user(
        db_session, login_id=f"ev_dg_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"문서 {quote}"
    )

    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.affiliation_company",
                "field_name": "affiliation_company",
                "change_type": "NEW",
                "new_value": {
                    "value": "TalentScope",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )
    assert _confirm(client, csrf, analysis.id).status_code == 200

    listed = client.get(f"/api/v1/analyses/{analysis.id}/diffs")
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    target = next(r for r in rows if r["id"] == str(diffs[0].id))
    assert target["evidence"]
    assert len(target["evidence"]) == 1
    ev = target["evidence"][0]
    assert ev["id"] is not None
    uuid.UUID(ev["id"])
    assert ev["document_id"] == str(document.id)
    assert ev["page_no"] == 1
    assert ev["quote_text"] == quote

    _cleanup_person(db_session, person.id, admin.id)


def test_evidence_insert_failure_rolls_back_confirm(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.db.models.person import PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.modules.evidence.repository import EvidenceRepository

    quote = "롤백 인용"
    admin = _create_user(
        db_session, login_id=f"ev_rb_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {quote}"
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.address_region",
                "field_name": "address_region",
                "change_type": "NEW",
                "new_value": {
                    "value": "서울",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    def boom(self, row):  # noqa: ANN001
        raise RuntimeError("evidence insert failed")

    monkeypatch.setattr(EvidenceRepository, "add_evidence", boom)

    with pytest.raises(RuntimeError, match="evidence insert failed"):
        from app.modules.analysis.service import AnalysisService
        from app.storage.s3 import get_object_storage

        service = AnalysisService(db_session, storage=get_object_storage())
        service.confirm_analysis(
            analysis.id,
            expected_profile_version=1,
            actor_user_id=admin.id,
        )

    db_session.expire_all()
    assert _evidence_counts(db_session, document_id=document.id) == {
        "evidence": 0,
        "links": 0,
        "diff_evidence": 0,
    }
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.profile_version == 1
    assert profile.address_region is None
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == analysis.id)
    ).scalar_one()
    assert run.status == "REVIEWING"
    assert (
        db_session.execute(
            select(ProfileRevision).where(
                ProfileRevision.person_id == person.id,
                ProfileRevision.revision_no == 2,
            )
        ).scalar_one_or_none()
        is None
    )

    _cleanup_person(db_session, person.id, admin.id)


def test_revision_failure_rolls_back_evidence(
    client: TestClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models.analysis import AnalysisRun
    from app.db.models.person import PersonProfile
    from app.modules.people.repository import PeopleRepository

    quote = "리비전 실패 인용"
    admin = _create_user(
        db_session, login_id=f"ev_rr_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {quote}"
    )

    analysis, _diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.employment_type",
                "field_name": "employment_type",
                "change_type": "NEW",
                "new_value": {
                    "value": "정규직",
                    "source_refs": [_source_ref(document, quote_text=quote)],
                },
                "review_status": "ACCEPTED",
            }
        ],
    )

    def boom(self, **_kwargs):  # noqa: ANN001
        raise RuntimeError("revision insert failed")

    monkeypatch.setattr(PeopleRepository, "add_revision", boom)

    with pytest.raises(RuntimeError, match="revision insert failed"):
        from app.modules.analysis.service import AnalysisService
        from app.storage.s3 import get_object_storage

        service = AnalysisService(db_session, storage=get_object_storage())
        service.confirm_analysis(
            analysis.id,
            expected_profile_version=1,
            actor_user_id=admin.id,
        )

    db_session.expire_all()
    assert _evidence_counts(db_session, document_id=document.id) == {
        "evidence": 0,
        "links": 0,
        "diff_evidence": 0,
    }
    profile = db_session.execute(
        select(PersonProfile).where(PersonProfile.person_id == person.id)
    ).scalar_one()
    assert profile.profile_version == 1
    run = db_session.execute(
        select(AnalysisRun).where(AnalysisRun.id == analysis.id)
    ).scalar_one()
    assert run.status == "REVIEWING"

    _cleanup_person(db_session, person.id, admin.id)


# --------------------------------------------------------------------------- real pipeline provenance


def _persist_specs_as_reviewing(
    db_session,
    *,
    person,
    document,
    specs,
    accept_paths: set[str] | None = None,
):
    """Persist DiffSpec list onto a REVIEWING AnalysisRun (real Diff persistence)."""
    from app.db.models.analysis import AnalysisDiffItem, AnalysisRun, AnalysisRunDocument

    analysis = AnalysisRun(
        person_id=person.id,
        status="REVIEWING",
        base_profile_version=1,
        prompt_version="profile-extract-v2",
        schema_version="profile-candidate-v1",
        candidate_json={"schema_version": "profile-candidate-v1"},
    )
    db_session.add(analysis)
    db_session.flush()
    db_session.add(
        AnalysisRunDocument(analysis_run_id=analysis.id, document_id=document.id)
    )
    created = []
    accept_paths = accept_paths or set()
    for spec in specs:
        payload = spec.to_persist_dict()
        if payload.get("candidate_path") in accept_paths:
            payload["review_status"] = "ACCEPTED"
        elif payload.get("change_type") == "SAME":
            payload["review_status"] = "PENDING"
        row = AnalysisDiffItem(analysis_run_id=analysis.id, **payload)
        db_session.add(row)
        created.append(row)
    db_session.commit()
    for row in created:
        db_session.refresh(row)
    db_session.refresh(analysis)
    return analysis, created


def test_real_pipeline_profile_scalar_source_refs_to_evidence(
    client: TestClient, db_session
):
    """raw Candidate → normalize → build_diffs → Confirm → Evidence (no manual Diff refs)."""
    from app.modules.analysis.diff_engine import build_diffs
    from app.modules.analysis.normalize import normalize_candidate
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    quote = "성명 홍길동"
    admin = _create_user(
        db_session, login_id=f"ev_pp_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"이력서 머리말 {quote} / 연락처"
    )

    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {
            "name": "홍길동",
            "source_refs": {
                "name": [
                    {
                        "document_id": str(document.id),
                        "page_no": 1,
                        "quote_text": quote,
                    }
                ],
                "not_a_field": [{"document_id": str(document.id), "page_no": 1, "quote_text": quote}],
            },
        },
    }
    catalog: dict[str, tuple[str, bool]] = {}
    allowed = {str(document.id): {1}}
    page_texts = {(str(document.id), 1): f"이력서 머리말 {quote} / 연락처"}
    doc = normalize_candidate(
        raw,
        catalog=catalog,
        allowed_documents=allowed,
        page_texts=page_texts,
    )
    assert "name" in doc.profile.source_refs
    assert "not_a_field" not in doc.profile.source_refs
    assert doc.profile.source_refs["name"][0].quote_text == quote

    snap = build_confirmed_profile_snapshot(db_session, person.id)
    specs = build_diffs(doc, snap)
    name_specs = [s for s in specs if s.field_name == "name"]
    assert len(name_specs) == 1
    assert name_specs[0].source_refs
    assert name_specs[0].source_refs[0]["quote_text"] == quote
    # Must come from Candidate map, not manually injected Diff.new_value.
    assert "source_refs" not in (name_specs[0].new_value if isinstance(name_specs[0].new_value, dict) else {})

    analysis, diffs = _persist_specs_as_reviewing(
        db_session,
        person=person,
        document=document,
        specs=specs,
        accept_paths={"profile.name"},
    )
    name_diff = next(d for d in diffs if d.field_name == "name")
    assert isinstance(name_diff.new_value, dict)
    assert name_diff.new_value.get("source_refs")

    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    counts = _evidence_counts(db_session, document_id=document.id)
    assert counts["evidence"] >= 1
    assert counts["diff_evidence"] >= 1
    assert counts["links"] >= 1
    ev = _list_evidence_for_doc(db_session, document.id)[0]
    assert ev.quote_text == quote
    links = _links_for_evidence(db_session, ev.id)
    assert any(
        link.target_type == "PERSON_PROFILE"
        and link.target_id == person.id
        and link.field_name == "name"
        for link in links
    )
    assert _diff_evidence_for(db_session, name_diff.id)

    _cleanup_person(db_session, person.id, admin.id)


def test_real_pipeline_project_relation_item_refs_not_root(
    client: TestClient, db_session
):
    from app.db.models.project import Project, ProjectSkill
    from app.modules.analysis.diff_engine import build_diffs
    from app.modules.analysis.normalize import normalize_candidate
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    _ensure_common_codes(db_session)
    root_quote = "AI 플랫폼 구축 프로젝트"
    py_quote = "Python/FastAPI 기반 API 개발"
    admin = _create_user(
        db_session, login_id=f"ev_prp_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    page_text = f"소개 {root_quote}. 기술스택 {py_quote}."
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=page_text
    )
    project = Project(
        person_id=person.id,
        project_name="AI 플랫폼",
        source_type="USER",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    raw = {
        "schema_version": "profile-candidate-v1",
        "projects": [
            {
                "project_name": "AI 플랫폼",
                "source_refs": [
                    {
                        "document_id": str(document.id),
                        "page_no": 1,
                        "quote_text": root_quote,
                    }
                ],
                "skills": [
                    {
                        "raw_value": "Python",
                        "code": "TECH-LANG-PYTHON",
                        "source_refs": [
                            {
                                "document_id": str(document.id),
                                "page_no": 1,
                                "quote_text": py_quote,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    catalog = {"TECH-LANG-PYTHON": ("TECH", True)}
    allowed = {str(document.id): {1}}
    page_texts = {(str(document.id), 1): page_text}
    cand = normalize_candidate(
        raw, catalog=catalog, allowed_documents=allowed, page_texts=page_texts
    )
    assert cand.projects[0].skills[0].source_refs[0].quote_text == py_quote

    snap = build_confirmed_profile_snapshot(db_session, person.id)
    specs = build_diffs(cand, snap)
    skill_specs = [
        s
        for s in specs
        if s.field_name == "skills" and s.change_type == "UPDATE"
    ]
    assert len(skill_specs) == 1
    quotes = {r.get("quote_text") for r in skill_specs[0].source_refs}
    assert py_quote in quotes
    assert root_quote not in quotes

    analysis, diffs = _persist_specs_as_reviewing(
        db_session,
        person=person,
        document=document,
        specs=specs,
        accept_paths={skill_specs[0].candidate_path},
    )
    skill_diff = next(
        d for d in diffs if d.field_name == "skills" and d.change_type == "UPDATE"
    )
    resp = _confirm(client, csrf, analysis.id)
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    assert db_session.execute(
        select(ProjectSkill).where(
            ProjectSkill.project_id == project.id,
            ProjectSkill.tech_code == "TECH-LANG-PYTHON",
        )
    ).scalar_one_or_none()

    links = []
    for ev in _list_evidence_for_doc(db_session, document.id):
        for link in _links_for_evidence(db_session, ev.id):
            if link.field_name == "skills:TECH-LANG-PYTHON":
                links.append((ev, link))
    assert len(links) == 1
    assert links[0][0].quote_text == py_quote
    assert links[0][0].quote_text != root_quote

    _cleanup_person(db_session, person.id, admin.id)


def test_relation_without_item_refs_no_field_evidence_link(
    client: TestClient, db_session
):
    from app.db.models.project import Project, ProjectSkill
    from app.modules.analysis.diff_engine import build_diffs
    from app.modules.analysis.normalize import normalize_candidate
    from app.modules.people.snapshot import build_confirmed_profile_snapshot

    _ensure_common_codes(db_session)
    root_quote = "플랫폼 전체 설명 문구"
    admin = _create_user(
        db_session, login_id=f"ev_norel_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    page_text = f"본문 {root_quote}"
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=page_text
    )
    project = Project(
        person_id=person.id,
        project_name="무근거 스킬 프로젝트",
        source_type="USER",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    raw = {
        "schema_version": "profile-candidate-v1",
        "projects": [
            {
                "project_name": "무근거 스킬 프로젝트",
                "source_refs": [
                    {
                        "document_id": str(document.id),
                        "page_no": 1,
                        "quote_text": root_quote,
                    }
                ],
                "skills": [
                    {
                        "raw_value": "Python",
                        "code": "TECH-LANG-PYTHON",
                        "source_refs": [],
                    }
                ],
            }
        ],
    }
    cand = normalize_candidate(
        raw,
        catalog={"TECH-LANG-PYTHON": ("TECH", True)},
        allowed_documents={str(document.id): {1}},
        page_texts={(str(document.id), 1): page_text},
    )
    specs = build_diffs(cand, build_confirmed_profile_snapshot(db_session, person.id))
    skill_specs = [s for s in specs if s.field_name == "skills" and s.change_type == "UPDATE"]
    assert len(skill_specs) == 1
    assert skill_specs[0].source_refs == []

    analysis, _diffs = _persist_specs_as_reviewing(
        db_session,
        person=person,
        document=document,
        specs=specs,
        accept_paths={skill_specs[0].candidate_path},
    )
    assert _confirm(client, csrf, analysis.id).status_code == 200

    db_session.expire_all()
    assert db_session.execute(
        select(ProjectSkill).where(
            ProjectSkill.project_id == project.id,
            ProjectSkill.tech_code == "TECH-LANG-PYTHON",
        )
    ).scalar_one_or_none()
    skill_links = [
        link
        for ev in _list_evidence_for_doc(db_session, document.id)
        for link in _links_for_evidence(db_session, ev.id)
        if link.field_name == "skills:TECH-LANG-PYTHON"
    ]
    assert skill_links == []

    _cleanup_person(db_session, person.id, admin.id)


def test_confirmed_diff_no_raw_fallback_for_invalid_or_empty_refs(
    client: TestClient, db_session
):
    quote = "유효한 인용문"
    admin = _create_user(
        db_session, login_id=f"ev_fb_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"문서 {quote}"
    )

    # Mixed refs Diff → only valid materialized.
    analysis, diffs = _seed_reviewing_run(
        db_session,
        person=person,
        document=document,
        diffs=[
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.department",
                "field_name": "department",
                "change_type": "NEW",
                "new_value": {
                    "value": "플랫폼팀",
                    "source_refs": [
                        _source_ref(document, quote_text=quote),
                        {
                            "document_id": str(document.id),
                            "page_no": 99,
                            "quote_text": "없는 페이지",
                        },
                    ],
                },
                "review_status": "ACCEPTED",
            },
            {
                "entity_type": "PROFILE",
                "candidate_path": "profile.phone",
                "field_name": "phone",
                "change_type": "NEW",
                "new_value": {
                    "value": "010-0000-0000",
                    "source_refs": [
                        {
                            "document_id": str(document.id),
                            "page_no": 99,
                            "quote_text": "전부 무효",
                        }
                    ],
                },
                "review_status": "ACCEPTED",
            },
        ],
    )
    # REVIEWING may expose raw refs (incl. invalid) via fallback.
    reviewing = client.get(f"/api/v1/analyses/{analysis.id}/diffs")
    assert reviewing.status_code == 200
    before = {row["id"]: row["evidence"] for row in reviewing.json()["data"]}
    assert len(before[str(diffs[0].id)]) == 2
    assert all(item.get("id") is None for item in before[str(diffs[0].id)])

    assert _confirm(client, csrf, analysis.id).status_code == 200

    confirmed = client.get(f"/api/v1/analyses/{analysis.id}/diffs")
    assert confirmed.status_code == 200
    after = {row["id"]: row["evidence"] for row in confirmed.json()["data"]}
    dept_ev = after[str(diffs[0].id)]
    assert len(dept_ev) == 1
    assert dept_ev[0]["id"] is not None
    assert dept_ev[0]["quote_text"] == quote
    assert dept_ev[0]["page_no"] == 1
    # All-invalid Diff → empty list, never raw id=null fallback.
    assert after[str(diffs[1].id)] == []

    _cleanup_person(db_session, person.id, admin.id)


def test_old_candidate_without_new_provenance_fields_loads():
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument

    raw = {
        "schema_version": "profile-candidate-v1",
        "profile": {"name": "구버전"},
        "projects": [
            {
                "project_name": "레거시",
                "skills": [{"raw_value": "Python", "code": "TECH-LANG-PYTHON"}],
            }
        ],
    }
    doc = ProfileCandidateDocument.model_validate(raw)
    assert doc.profile.source_refs == {}
    assert doc.projects[0].skills[0].source_refs == []


def test_new_analysis_uses_profile_extract_v2(client: TestClient, db_session):
    from app.ai.prompts.profile_extract_v2 import PROMPT_VERSION

    admin = _create_user(
        db_session, login_id=f"ev_pv_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(db_session, admin.id)
    create = client.post(
        "/api/v1/analyses",
        headers={"X-CSRF-Token": csrf},
        json={"person_id": str(person.id), "document_ids": [str(document.id)]},
    )
    assert create.status_code in {200, 202}, create.text
    analysis_id = create.json()["data"]["analysis_id"]
    detail = client.get(f"/api/v1/analyses/{analysis_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["prompt_version"] == PROMPT_VERSION
    assert PROMPT_VERSION == "profile-extract-v2"

    _cleanup_person(db_session, person.id, admin.id)


def test_evidence_list_dedupes_same_evidence_without_field_filter(
    client: TestClient, db_session
):
    from app.db.models.evidence import Evidence, EvidenceLink

    quote = "공유 인용"
    admin = _create_user(
        db_session, login_id=f"ev_dd_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    csrf = _login(client, admin.login_id, "Passw0rd!")
    person, document = _seed_person_with_ready_doc(
        db_session, admin.id, page_text=f"본문 {quote}"
    )
    from app.db.models.document import DocumentPage

    page = db_session.execute(
        select(DocumentPage).where(DocumentPage.document_id == document.id)
    ).scalar_one()
    ev = Evidence(
        document_id=document.id,
        document_page_id=page.id,
        page_no=1,
        quote_text=quote,
        extraction_method="TEXT_PARSER",
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(
        EvidenceLink(
            evidence_id=ev.id,
            target_type="PERSON_PROFILE",
            target_id=person.id,
            field_name="name",
            relation_type="SUPPORTS",
        )
    )
    db_session.add(
        EvidenceLink(
            evidence_id=ev.id,
            target_type="PERSON_PROFILE",
            target_id=person.id,
            field_name="phone",
            relation_type="SUPPORTS",
        )
    )
    db_session.commit()

    listed = client.get(
        "/api/v1/evidence",
        params={"target_type": "PERSON_PROFILE", "target_id": str(person.id)},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["id"] == str(ev.id)

    by_field = client.get(
        "/api/v1/evidence",
        params={
            "target_type": "PERSON_PROFILE",
            "target_id": str(person.id),
            "field_name": "name",
        },
    )
    assert by_field.status_code == 200
    assert len(by_field.json()["data"]) == 1

    _cleanup_person(db_session, person.id, admin.id)


def test_profile_extract_v2_template_has_provenance_keys():
    from app.ai.prompts.profile_extract_v2 import (
        CANDIDATE_JSON_TEMPLATE,
        PROMPT_VERSION,
        SYSTEM_PROMPT,
    )

    assert PROMPT_VERSION == "profile-extract-v2"
    assert '"source_refs"' in CANDIDATE_JSON_TEMPLATE
    assert '"name": []' in CANDIDATE_JSON_TEMPLATE
    assert '"source_refs": []' in CANDIDATE_JSON_TEMPLATE
    assert "relation" in SYSTEM_PROMPT.lower() or "Project relation" in SYSTEM_PROMPT
    assert "profile.source_refs" in SYSTEM_PROMPT
