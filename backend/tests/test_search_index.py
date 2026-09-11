"""Search Index Worker + Confirmed Search Document Builder tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ.setdefault("APP_ENV", "test")


@pytest.fixture()
def redis_prefix() -> str:
    return f"talentscope:test:{uuid.uuid4().hex}"


@pytest.fixture()
def client(redis_prefix: str) -> Generator[TestClient, None, None]:
    os.environ["REDIS_KEY_PREFIX"] = redis_prefix
    os.environ["APP_ENV"] = "test"

    from app.core.config import get_settings
    from app.core.redis import get_redis
    from app.main import create_app

    get_settings.cache_clear()
    get_redis.cache_clear()
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client

    redis = get_redis()
    keys = list(redis.scan_iter(match=f"{redis_prefix}:*"))
    if keys:
        redis.delete(*keys)
    get_settings.cache_clear()
    get_redis.cache_clear()


@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _ensure_code(db, code: str, code_type: str, name: str) -> None:
    from app.db.models.code import CodeMaster

    if db.get(CodeMaster, code) is None:
        db.add(
            CodeMaster(
                code=code,
                code_type=code_type,
                name=name,
                sort_order=0,
                is_active=True,
            )
        )
        db.commit()


def _cleanup_person(db, person_id) -> None:
    from app.db.models.person import (
        Certification,
        Education,
        EmploymentHistory,
        Person,
        PersonExpertise,
        PersonJob,
        PersonProfile,
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
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexItem, SearchIndexJob

    project_ids = list(
        db.scalars(select(Project.id).where(Project.person_id == person_id)).all()
    )
    if project_ids:
        db.execute(delete(ProjectJob).where(ProjectJob.project_id.in_(project_ids)))
        db.execute(delete(ProjectSkill).where(ProjectSkill.project_id.in_(project_ids)))
        db.execute(
            delete(ProjectExpertise).where(ProjectExpertise.project_id.in_(project_ids))
        )
        db.execute(
            delete(ProjectBusinessDomain).where(
                ProjectBusinessDomain.project_id.in_(project_ids)
            )
        )
        db.execute(
            delete(ProjectCustomerType).where(
                ProjectCustomerType.project_id.in_(project_ids)
            )
        )
        db.execute(delete(Project).where(Project.person_id == person_id))

    db.execute(delete(SearchIndexItem).where(SearchIndexItem.person_id == person_id))
    db.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db.execute(
        delete(AuditLog).where(
            AuditLog.target_type == "PERSON", AuditLog.target_id == person_id
        )
    )
    db.execute(delete(PersonJob).where(PersonJob.person_id == person_id))
    db.execute(delete(PersonSkill).where(PersonSkill.person_id == person_id))
    db.execute(delete(PersonExpertise).where(PersonExpertise.person_id == person_id))
    db.execute(delete(EmploymentHistory).where(EmploymentHistory.person_id == person_id))
    db.execute(delete(Education).where(Education.person_id == person_id))
    db.execute(delete(Certification).where(Certification.person_id == person_id))
    db.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db.execute(delete(Person).where(Person.id == person_id))
    db.commit()


def _seed_person(db, *, suffix: str | None = None):
    from app.db.models.person import (
        Certification,
        Education,
        EmploymentHistory,
        Person,
        PersonExpertise,
        PersonJob,
        PersonProfile,
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

    suffix = suffix or uuid.uuid4().hex[:8]
    codes = {
        "job": f"JOB-AI-{suffix}",
        "job2": f"JOB-PL-{suffix}",
        "tech": f"TECH-PY-{suffix}",
        "tech2": f"TECH-FA-{suffix}",
        "exp": f"EXP-RAG-{suffix}",
        "exp2": f"EXP-LLM-{suffix}",
        "biz": f"BIZ-PUB-{suffix}",
        "cust": f"CUST-GOV-{suffix}",
    }
    _ensure_code(db, codes["job"], "JOB", "AI 개발")
    _ensure_code(db, codes["job2"], "JOB", "PL")
    _ensure_code(db, codes["tech"], "TECH", "Python")
    _ensure_code(db, codes["tech2"], "TECH", "FastAPI")
    _ensure_code(db, codes["exp"], "EXP", "RAG")
    _ensure_code(db, codes["exp2"], "EXP", "LLM")
    _ensure_code(db, codes["biz"], "BIZ", "공공")
    _ensure_code(db, codes["cust"], "CUSTOMER_TYPE", "공공기관")

    person = Person(status="ACTIVE")
    db.add(person)
    db.flush()

    profile = PersonProfile(
        person_id=person.id,
        name=f"홍길동_{suffix}",
        phone="010-1234-5678",
        email="secret@example.com",
        address_region="서울시 강남구",
        birth_year=1988,
        affiliation_company="오픈링크시스템",
        department="기술연구소",
        current_title="책임연구원",
        employment_type="정규직",
        technical_grade="EXPERT",
        career_confirmed_months=192,
        career_calculated_months=190,
        career_document_value="문서상 16년",
        profile_summary="AI/Backend 전문 인력",
        profile_version=3,
    )
    db.add(profile)
    db.add(PersonJob(person_id=person.id, job_code=codes["job"], job_type="PRIMARY", sort_order=0, source_type="USER"))
    db.add(PersonJob(person_id=person.id, job_code=codes["job2"], job_type="SECONDARY", sort_order=1, source_type="USER"))
    db.add(PersonSkill(person_id=person.id, tech_code=codes["tech"], is_representative=True, source_type="USER"))
    db.add(PersonSkill(person_id=person.id, tech_code=codes["tech2"], is_representative=False, source_type="USER"))
    db.add(PersonExpertise(person_id=person.id, exp_code=codes["exp"], evidence_type="EXPLICIT", source_type="USER"))
    db.add(
        EmploymentHistory(
            person_id=person.id,
            company_name="ABC테크",
            title="AI Engineer",
            start_date=date(2018, 1, 1),
            end_date=date(2020, 12, 31),
            responsibilities="AI 서비스 설계 및 개발",
            source_type="USER",
        )
    )
    db.add(Education(person_id=person.id, school_name="OO대학교", major="컴퓨터공학", degree="학사", source_type="USER"))
    db.add(
        Certification(
            person_id=person.id,
            certification_name="정보처리기사",
            issuer="한국산업인력공단",
            certificate_no="SECRET-CERT-999",
            source_type="USER",
        )
    )

    project_a = Project(
        person_id=person.id,
        project_name="군 의료 AI 플랫폼",
        customer_name="국군수도병원",
        start_date=date(2026, 8, 1),
        end_date=date(2027, 8, 16),
        duration_months=12,
        responsibilities="PL / Backend 개발",
        project_summary="의료 LLM 플랫폼",
        source_type="AI_CONFIRMED",
    )
    project_b = Project(
        person_id=person.id,
        project_name="공공 검색 고도화",
        customer_name="OO기관",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        duration_months=12,
        responsibilities="검색 파이프라인",
        project_summary="FTS/Vector",
        source_type="USER",
    )
    db.add_all([project_a, project_b])
    db.flush()
    db.add(ProjectJob(project_id=project_a.id, job_code=codes["job2"]))
    db.add(ProjectSkill(project_id=project_a.id, tech_code=codes["tech"]))
    db.add(ProjectSkill(project_id=project_a.id, tech_code=codes["tech2"]))
    db.add(ProjectExpertise(project_id=project_a.id, exp_code=codes["exp"], evidence_type="EXPLICIT"))
    db.add(ProjectExpertise(project_id=project_a.id, exp_code=codes["exp2"], evidence_type="INFERRED"))
    db.add(ProjectBusinessDomain(project_id=project_a.id, biz_code=codes["biz"]))
    db.add(ProjectCustomerType(project_id=project_a.id, customer_type_code=codes["cust"]))
    db.commit()
    db.refresh(person)
    db.refresh(profile)
    db.refresh(project_a)
    db.refresh(project_b)
    return {
        "person": person,
        "profile": profile,
        "project_a": project_a,
        "project_b": project_b,
        "codes": codes,
        "suffix": suffix,
    }


def _enqueue_job(db, person_id, profile_version: int, *, key_suffix: str):
    from app.db.models.search import SearchIndexJob

    job = SearchIndexJob(
        person_id=person_id,
        action="REBUILD_PERSON",
        status="PENDING",
        idempotency_key=f"people:{person_id}:{key_suffix}:rebuild",
        payload_json={"profile_version": profile_version},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_profile_and_project_search_documents(db_session) -> None:
    from app.modules.people.snapshot import build_confirmed_profile_snapshot
    from app.modules.search.document_builder import (
        build_search_documents_for_person,
        content_hash,
    )
    from app.modules.search.schemas import SEARCH_DOCUMENT_VERSION

    seeded = _seed_person(db_session)
    person = seeded["person"]
    codes = seeded["codes"]
    try:
        snapshot = build_confirmed_profile_snapshot(db_session, person.id)
        docs = build_search_documents_for_person(
            person_id=person.id,
            person_status=person.status,
            snapshot=snapshot,
        )
        assert len(docs) == 3
        profile_doc = docs[0]
        assert profile_doc.object_type == "PROFILE"
        assert profile_doc.object_id == person.id
        assert profile_doc.source_weight == Decimal("1.000")
        text = profile_doc.search_text
        assert "홍길동_" in text
        assert "오픈링크시스템" in text
        assert "EXPERT / 특급" in text
        assert "192개월" in text
        assert "AI 개발" in text
        assert "Python" in text
        assert "RAG" in text
        assert "ABC테크" in text
        assert "OO대학교" in text
        assert "정보처리기사" in text
        assert "군 의료 AI 플랫폼" not in text
        assert "의료 LLM 플랫폼" not in text
        assert "010-1234-5678" not in text
        assert "secret@example.com" not in text
        assert "서울시 강남구" not in text
        assert "1988" not in text
        assert "SECRET-CERT-999" not in text
        assert "문서상 16년" not in text

        meta = profile_doc.metadata
        assert meta["search_document_version"] == SEARCH_DOCUMENT_VERSION
        assert meta["content_hash"] == content_hash(profile_doc.search_text)
        assert meta["profile_version"] == 3
        assert meta["person_status"] == "ACTIVE"
        assert meta["technical_grade"] == "EXPERT"
        assert meta["career_months"] == 192
        assert codes["job"] in meta["job_codes"]
        assert codes["tech"] in meta["skill_codes"]
        assert codes["exp"] in meta["expertise_codes"]
        assert "phone" not in meta and "email" not in meta

        project_doc = next(d for d in docs if d.object_id == seeded["project_a"].id)
        assert project_doc.object_type == "PROJECT"
        assert "군 의료 AI 플랫폼" in project_doc.search_text
        assert "Python" in project_doc.search_text
        assert project_doc.metadata["content_hash"] == content_hash(project_doc.search_text)
        assert project_doc.source_weight == Decimal("1.000")
        assert any(
            e["code"] == codes["exp2"] and e["evidence_type"] == "INFERRED"
            for e in project_doc.metadata["expertise"]
        )
    finally:
        _cleanup_person(db_session, person.id)


def test_search_document_determinism(db_session) -> None:
    from app.modules.people.snapshot import build_confirmed_profile_snapshot
    from app.modules.search.document_builder import build_search_documents_for_person

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        snap = build_confirmed_profile_snapshot(db_session, person.id)
        docs1 = build_search_documents_for_person(
            person_id=person.id, person_status="ACTIVE", snapshot=snap
        )
        docs2 = build_search_documents_for_person(
            person_id=person.id, person_status="ACTIVE", snapshot=snap
        )
        for a, b in zip(docs1, docs2, strict=True):
            assert a.search_text == b.search_text
            assert a.metadata == b.metadata
    finally:
        _cleanup_person(db_session, person.id)


def test_rebuild_person_integration(db_session) -> None:
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="rebuild-int")
        result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "COMPLETED"
        assert result.claimed is True

        db_session.expire_all()
        job2 = db_session.get(SearchIndexJob, job.id)
        assert job2 is not None and job2.status == "COMPLETED"
        assert job2.error_message is None

        items = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person.id,
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(items) == 3
        assert sorted(i.object_type for i in items) == ["PROFILE", "PROJECT", "PROJECT"]
        for item in items:
            assert item.embedding is None
            assert item.embedding_model is None
            assert item.embedding_version is None
            assert item.source_weight == Decimal("1.000")
            assert item.metadata_json.get("content_hash")
    finally:
        _cleanup_person(db_session, person.id)


def test_duplicate_process_no_extra_rows(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="dup")
        service = SearchIndexService(db_session)
        first = service.process_job(job.id)
        second = service.process_job(job.id)
        assert first.claimed is True and first.status == "COMPLETED"
        assert second.claimed is False and second.status == "COMPLETED"
        rows = list(
            db_session.scalars(
                select(SearchIndexItem).where(SearchIndexItem.person_id == person.id)
            ).all()
        )
        assert len(rows) == 3
    finally:
        _cleanup_person(db_session, person.id)


def test_concurrent_claim_only_one_wins(db_session) -> None:
    from app.db.session import SessionLocal
    from app.modules.search.repository import SearchRepository

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="claim")
        s1 = SessionLocal()
        s2 = SessionLocal()
        try:
            c1 = SearchRepository(s1).claim_pending_job(job.id)
            assert c1 is not None
            s1.commit()
            c2 = SearchRepository(s2).claim_pending_job(job.id)
            assert c2 is None
            s2.rollback()
        finally:
            s1.close()
            s2.close()

        db_session.expire_all()
        row = db_session.get(type(job), job.id)
        assert row is not None and row.status == "PROCESSING"
        row.status = "PENDING"
        row.started_at = None
        db_session.add(row)
        db_session.commit()
    finally:
        _cleanup_person(db_session, person.id)


def test_claim_without_commit_leaves_pending(db_session) -> None:
    from app.db.models.search import SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.repository import SearchRepository

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="hardkill")
        s = SessionLocal()
        try:
            claimed = SearchRepository(s).claim_pending_job(job.id)
            assert claimed is not None
            assert claimed.status == "PROCESSING"
            s.rollback()
        finally:
            s.close()
        db_session.expire_all()
        row = db_session.get(SearchIndexJob, job.id)
        assert row is not None and row.status == "PENDING"
    finally:
        _cleanup_person(db_session, person.id)


def test_project_delete_deactivates_index(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    project_b = seeded["project_b"]
    try:
        job1 = _enqueue_job(db_session, person.id, 3, key_suffix="before-del")
        SearchIndexService(db_session).process_job(job1.id)

        project_b.deleted_at = datetime.now(UTC)
        seeded["profile"].profile_version = 4
        db_session.add_all([project_b, seeded["profile"]])
        db_session.commit()

        job2 = _enqueue_job(db_session, person.id, 4, key_suffix="after-del")
        SearchIndexService(db_session).process_job(job2.id)

        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person.id,
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 2
        assert any(i.object_id == seeded["project_a"].id for i in active)
        inactive_b = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.object_id == project_b.id,
                SearchIndexItem.object_type == "PROJECT",
            )
        ).first()
        assert inactive_b is not None and inactive_b.is_active is False
    finally:
        _cleanup_person(db_session, person.id)


def test_person_delete_and_restore_index(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.people.repository import PeopleRepository
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    profile = seeded["profile"]
    try:
        job1 = _enqueue_job(db_session, person.id, 3, key_suffix="before-status")
        SearchIndexService(db_session).process_job(job1.id)

        person.status = "DELETED"
        person.deleted_at = datetime.now(UTC)
        person.updated_at = datetime.now(UTC)
        db_session.add(person)
        db_session.flush()
        PeopleRepository(db_session).enqueue_rebuild_person(
            person.id,
            profile.profile_version,
            reason="PERSON_STATUS",
            idempotency_suffix=f"status:DELETED:{person.updated_at.isoformat()}",
        )
        db_session.commit()

        del_job = db_session.scalars(
            select(type(job1)).where(
                type(job1).person_id == person.id,
                type(job1).status == "PENDING",
            )
        ).one()
        SearchIndexService(db_session).process_job(del_job.id)
        assert (
            list(
                db_session.scalars(
                    select(SearchIndexItem).where(
                        SearchIndexItem.person_id == person.id,
                        SearchIndexItem.is_active.is_(True),
                    )
                ).all()
            )
            == []
        )

        version_before = profile.profile_version
        person.status = "ACTIVE"
        person.deleted_at = None
        person.updated_at = datetime.now(UTC)
        db_session.add(person)
        db_session.flush()
        PeopleRepository(db_session).enqueue_rebuild_person(
            person.id,
            profile.profile_version,
            reason="PERSON_STATUS",
            idempotency_suffix=f"status:ACTIVE:{person.updated_at.isoformat()}",
        )
        db_session.commit()
        rest_job = db_session.scalars(
            select(type(job1)).where(
                type(job1).person_id == person.id,
                type(job1).status == "PENDING",
            )
        ).one()
        SearchIndexService(db_session).process_job(rest_job.id)
        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person.id,
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 3
        db_session.refresh(profile)
        assert profile.profile_version == version_before
    finally:
        _cleanup_person(db_session, person.id)


def test_document_chunk_preserved(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        chunk = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="DOCUMENT_CHUNK",
            object_id=uuid.uuid4(),
            search_text="chunk body",
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={"kind": "chunk"},
            is_active=True,
        )
        db_session.add(chunk)
        db_session.commit()

        job = _enqueue_job(db_session, person.id, 3, key_suffix="chunk")
        SearchIndexService(db_session).process_job(job.id)

        db_session.expire_all()
        kept = db_session.get(SearchIndexItem, chunk.id)
        assert kept is not None
        assert kept.is_active is True
        assert kept.search_text == "chunk body"
    finally:
        _cleanup_person(db_session, person.id)


def test_embedding_invalidate_and_preserve(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_builder import content_hash
    from app.modules.search.schemas import SEARCH_DOCUMENT_VERSION
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    profile = seeded["profile"]
    dummy = [0.01] * 1024
    try:
        old_item = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            search_text="OLD",
            embedding=dummy,
            source_weight=Decimal("1.000"),
            metadata_json={
                "search_document_version": SEARCH_DOCUMENT_VERSION,
                "content_hash": content_hash("OLD"),
                "profile_version": 2,
            },
            embedding_model="test-model",
            embedding_version="v1",
            is_active=True,
        )
        db_session.add(old_item)
        db_session.commit()

        job1 = _enqueue_job(db_session, person.id, 3, key_suffix="invalidate")
        SearchIndexService(db_session).process_job(job1.id)

        item = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == person.id,
                SearchIndexItem.object_type == "PROFILE",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert item.search_text != "OLD"
        assert item.embedding is None
        assert item.embedding_model is None
        assert item.embedding_version is None

        item.embedding = dummy
        item.embedding_model = "test-model"
        item.embedding_version = "v1"
        preserved_text = item.search_text
        profile.profile_version = 4
        db_session.add_all([item, profile])
        db_session.commit()

        job2 = _enqueue_job(db_session, person.id, 4, key_suffix="preserve")
        SearchIndexService(db_session).process_job(job2.id)

        item2 = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == person.id,
                SearchIndexItem.object_type == "PROFILE",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert item2.search_text == preserved_text
        assert item2.metadata_json["profile_version"] == 4
        assert item2.embedding_model == "test-model"
        assert item2.embedding_version == "v1"
        assert item2.embedding is not None
    finally:
        _cleanup_person(db_session, person.id)


def test_out_of_order_jobs_use_live_profile(db_session) -> None:
    from app.db.models.search import SearchIndexItem
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    profile = seeded["profile"]
    try:
        job_v2 = _enqueue_job(db_session, person.id, 2, key_suffix="stale-v2")
        profile.profile_version = 5
        profile.profile_summary = "live-v5-summary"
        db_session.add(profile)
        db_session.commit()
        job_v5 = _enqueue_job(db_session, person.id, 5, key_suffix="live-v5")

        SearchIndexService(db_session).process_job(job_v5.id)
        SearchIndexService(db_session).process_job(job_v2.id)

        item = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == person.id,
                SearchIndexItem.object_type == "PROFILE",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert item.metadata_json["profile_version"] == 5
        assert "live-v5-summary" in item.search_text
    finally:
        _cleanup_person(db_session, person.id)


def test_failed_job_rolls_back_partial_mutations(db_session) -> None:
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        existing = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            search_text="KEEP-ME",
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={"keep": True},
            is_active=True,
        )
        db_session.add(existing)
        db_session.commit()
        existing_id = existing.id

        job = _enqueue_job(db_session, person.id, 3, key_suffix="fail")
        service = SearchIndexService(db_session)
        from app.modules.search.errors import SearchIndexProcessingError

        with patch.object(
            service.repo,
            "upsert_search_document",
            side_effect=RuntimeError(
                "injected upsert failure secret@example.com 홍길동 SEARCH-TEXT-SECRET"
            ),
        ):
            with pytest.raises(SearchIndexProcessingError) as raised:
                service.process_job(job.id)

        safe = str(raised.value)
        assert "secret@example.com" not in safe
        assert "홍길동" not in safe
        assert "SEARCH-TEXT-SECRET" not in safe
        assert "injected upsert failure" not in safe

        db_session.expire_all()
        job_row = db_session.get(SearchIndexJob, job.id)
        assert job_row is not None
        assert job_row.status == "FAILED"
        assert job_row.retry_count == 1
        err = job_row.error_message or ""
        assert "secret@example.com" not in err
        assert "홍길동" not in err
        assert "SEARCH-TEXT-SECRET" not in err
        assert "injected upsert failure" not in err
        assert "Traceback" not in err
        assert "search index processing failed" in err

        kept = db_session.get(SearchIndexItem, existing_id)
        assert kept is not None
        assert kept.search_text == "KEEP-ME"
        assert kept.is_active is True
    finally:
        _cleanup_person(db_session, person.id)


def test_unsupported_action_fails(db_session) -> None:
    from app.db.models.search import SearchIndexJob
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = SearchIndexJob(
            person_id=person.id,
            action="UPSERT",
            status="PENDING",
            idempotency_key=f"people:{person.id}:upsert:{uuid.uuid4().hex}",
            payload_json={},
        )
        db_session.add(job)
        db_session.commit()
        result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "FAILED"
        row = db_session.get(SearchIndexJob, job.id)
        assert row is not None and row.status == "FAILED"
        assert "unsupported" in (row.error_message or "").lower()
    finally:
        _cleanup_person(db_session, person.id)


def test_dispatcher_reserves_and_publishes(db_session) -> None:
    from app.db.models.search import SearchIndexJob
    from app.modules.search.repository import SearchRepository
    from app.tasks.index_tasks import dispatch_pending_search_index_jobs

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        j1 = _enqueue_job(db_session, person.id, 1, key_suffix="d1")
        j2 = _enqueue_job(db_session, person.id, 2, key_suffix="d2")
        j3 = _enqueue_job(db_session, person.id, 3, key_suffix="d3")
        j4 = _enqueue_job(db_session, person.id, 4, key_suffix="d4")
        j4.status = "COMPLETED"
        j4.completed_at = datetime.now(UTC)
        db_session.add(j4)
        db_session.commit()

        target_ids = {j1.id, j2.id}
        original_reserve = SearchRepository.reserve_pending_jobs
        original_release = SearchRepository.release_reserved_job

        def reserve_only_targets(self, *, limit: int = 50):
            jobs = original_reserve(self, limit=500)
            selected = [job for job in jobs if job.id in target_ids][:limit]
            # Release any non-target jobs that were reserved from the shared DB.
            for job in jobs:
                if job.id not in {s.id for s in selected}:
                    original_release(self, job.id)
            self.db.flush()
            return selected

        with (
            patch.object(SearchRepository, "reserve_pending_jobs", reserve_only_targets),
            patch("app.tasks.index_tasks.process_search_index_job.delay") as delay_mock,
        ):
            delay_mock.return_value = MagicMock()
            out = dispatch_pending_search_index_jobs(limit=2)

        assert out["reserved"] == 2
        assert out["published"] == 2
        assert set(out["job_ids"]) == {str(j1.id), str(j2.id)}
        assert delay_mock.call_count == 2

        db_session.expire_all()
        for job_id, expected in (
            (j1.id, "PROCESSING"),
            (j2.id, "PROCESSING"),
            (j3.id, "PENDING"),
            (j4.id, "COMPLETED"),
        ):
            row = db_session.get(SearchIndexJob, job_id)
            assert row is not None and row.status == expected
            if expected == "PROCESSING":
                assert row.started_at is not None
    finally:
        _cleanup_person(db_session, person.id)


def test_status_mutation_enqueues_rebuild_without_version_bump(
    client: TestClient, db_session
) -> None:
    from app.core.security import hash_password
    from app.db.models.person import PersonProfile
    from app.db.models.revision import ProfileRevision
    from app.db.models.search import SearchIndexJob
    from app.db.models.user import AppUser

    suffix = uuid.uuid4().hex[:8]
    admin = AppUser(
        login_id=f"a_{suffix}",
        password_hash=hash_password("Secret123!"),
        name="Admin",
        role="ADMIN",
        status="ACTIVE",
    )
    db_session.add(admin)
    db_session.commit()

    seeded = _seed_person(db_session, suffix=suffix)
    person = seeded["person"]
    profile = seeded["profile"]
    version_before = profile.profile_version
    rev_before = db_session.scalar(
        select(ProfileRevision).where(ProfileRevision.person_id == person.id)
    )

    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_id": admin.login_id, "password": "Secret123!"},
        )
        assert login.status_code == 200
        csrf = client.cookies.get("ts_csrf")
        resp = client.patch(
            f"/api/v1/people/{person.id}",
            headers={"X-CSRF-Token": csrf},
            json={"status": "INACTIVE"},
        )
        assert resp.status_code == 200, resp.text

        db_session.expire_all()
        profile2 = db_session.get(PersonProfile, person.id)
        assert profile2 is not None
        assert profile2.profile_version == version_before
        assert (
            db_session.scalar(
                select(ProfileRevision).where(ProfileRevision.person_id == person.id)
            )
            is rev_before
        )
        jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert any(
            (j.payload_json or {}).get("reason") == "PERSON_STATUS"
            and j.idempotency_key
            and "status:INACTIVE:" in j.idempotency_key
            for j in jobs
        )
    finally:
        _cleanup_person(db_session, person.id)
        db_session.execute(delete(AppUser).where(AppUser.id == admin.id))
        db_session.commit()


def test_processing_visible_after_reserve_before_worker(db_session) -> None:
    from app.db.models.search import SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="visibility")
        claimed = SearchIndexService(db_session).repo.claim_pending_job(job.id)
        assert claimed is not None
        db_session.commit()

        other = SessionLocal()
        try:
            row = other.get(SearchIndexJob, job.id)
            assert row is not None
            assert row.status == "PROCESSING"
            assert row.started_at is not None
        finally:
            other.close()
    finally:
        _cleanup_person(db_session, person.id)


def test_duplicate_dispatch_does_not_republish(db_session) -> None:
    from app.modules.search.repository import SearchRepository
    from app.modules.search.service import SearchIndexService
    from app.tasks.index_tasks import dispatch_pending_search_index_jobs

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="dup-dispatch")
        target = {job.id}
        original_reserve = SearchRepository.reserve_pending_jobs
        original_release = SearchRepository.release_reserved_job

        def reserve_only_target(self, *, limit: int = 50):
            jobs = original_reserve(self, limit=500)
            selected = [j for j in jobs if j.id in target][:limit]
            for j in jobs:
                if j.id not in target:
                    original_release(self, j.id)
            self.db.flush()
            return selected

        with (
            patch.object(SearchRepository, "reserve_pending_jobs", reserve_only_target),
            patch("app.tasks.index_tasks.process_search_index_job.delay") as delay_mock,
        ):
            delay_mock.return_value = MagicMock()
            first = dispatch_pending_search_index_jobs(limit=10)
            second = dispatch_pending_search_index_jobs(limit=10)

        assert first["published"] == 1
        assert second["published"] == 0
        assert delay_mock.call_count == 1
    finally:
        _cleanup_person(db_session, person.id)


def test_concurrent_dispatcher_reservation(db_session) -> None:
    import threading

    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        jobs = [
            _enqueue_job(db_session, person.id, i, key_suffix=f"conc-d{i}")
            for i in range(1, 4)
        ]
        our_ids = {j.id for j in jobs}
        barrier = threading.Barrier(2)
        results: list[set] = []
        errors: list[BaseException] = []

        def worker() -> None:
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                reserved = SearchIndexService(db).reserve_pending_jobs(limit=500)
                results.append({j.id for j in reserved} & our_ids)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors
        assert len(results) == 2
        assert results[0].isdisjoint(results[1])
        assert results[0] | results[1] == our_ids
    finally:
        _cleanup_person(db_session, person.id)


def test_concurrent_worker_lock_single_rebuild(db_session) -> None:
    import threading
    import time

    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="conc-worker")
        assert SearchIndexService(db_session).repo.claim_pending_job(job.id) is not None
        db_session.commit()

        entered = threading.Event()
        release = threading.Event()
        rebuild_calls: list[int] = []
        results: list[str] = []
        errors: list[BaseException] = []
        original = SearchIndexService._rebuild_person

        def slow_rebuild(self, locked_job):
            rebuild_calls.append(1)
            entered.set()
            assert release.wait(timeout=5)
            return original(self, locked_job)

        def worker_a() -> None:
            db = SessionLocal()
            try:
                with patch.object(SearchIndexService, "_rebuild_person", slow_rebuild):
                    result = SearchIndexService(db).process_job(job.id)
                results.append(result.status)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        def worker_b() -> None:
            assert entered.wait(timeout=5)
            db = SessionLocal()
            try:
                with patch.object(SearchIndexService, "_rebuild_person", slow_rebuild):
                    result = SearchIndexService(db).process_job(job.id)
                results.append(f"skip:{result.status}:{result.claimed}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                release.set()
                db.close()

        t1 = threading.Thread(target=worker_a)
        t2 = threading.Thread(target=worker_b)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert not errors
        assert rebuild_calls == [1]
        assert "COMPLETED" in results
        assert any(r.startswith("skip:") and ":False" in r for r in results)
    finally:
        _cleanup_person(db_session, person.id)


def test_stale_processing_recovery_and_redispatch(db_session) -> None:
    from datetime import timedelta

    from app.db.models.search import SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from app.tasks.index_tasks import (
        dispatch_pending_search_index_jobs,
        recover_stale_search_index_jobs,
    )

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        stale = _enqueue_job(db_session, person.id, 3, key_suffix="stale")
        fresh = _enqueue_job(db_session, person.id, 3, key_suffix="fresh")
        now = datetime.now(UTC)
        stale.status = "PROCESSING"
        stale.started_at = now - timedelta(minutes=10)
        fresh.status = "PROCESSING"
        fresh.started_at = now
        db_session.add_all([stale, fresh])
        db_session.commit()
        stale_retry = stale.retry_count or 0

        out = recover_stale_search_index_jobs(limit=100)
        assert str(stale.id) in out["job_ids"]
        assert str(fresh.id) not in out["job_ids"]

        db_session.expire_all()
        stale_row = db_session.get(SearchIndexJob, stale.id)
        fresh_row = db_session.get(SearchIndexJob, fresh.id)
        assert stale_row is not None and stale_row.status == "PENDING"
        assert stale_row.started_at is None
        assert stale_row.retry_count == stale_retry + 1
        assert fresh_row is not None and fresh_row.status == "PROCESSING"

        target = {stale.id}
        from app.modules.search.repository import SearchRepository as _SR

        original_reserve = _SR.reserve_pending_jobs
        original_release = _SR.release_reserved_job

        def reserve_only_stale(self, *, limit: int = 50):
            jobs = original_reserve(self, limit=500)
            selected = [j for j in jobs if j.id in target][:limit]
            for j in jobs:
                if j.id not in target:
                    original_release(self, j.id)
            self.db.flush()
            return selected

        with (
            patch.object(_SR, "reserve_pending_jobs", reserve_only_stale),
            patch("app.tasks.index_tasks.process_search_index_job.delay") as delay_mock,
        ):
            delay_mock.return_value = MagicMock()
            dispatched = dispatch_pending_search_index_jobs(limit=10)

        assert dispatched["published"] == 1
        assert dispatched["job_ids"] == [str(stale.id)]

        result = SearchIndexService(db_session).process_job(stale.id)
        assert result.status == "COMPLETED"
        assert result.claimed is True
    finally:
        _cleanup_person(db_session, person.id)


def test_publish_failure_restores_pending(db_session) -> None:
    from app.db.models.search import SearchIndexJob
    from app.modules.search.repository import SearchRepository
    from app.modules.search.service import SearchIndexService
    from app.tasks.index_tasks import dispatch_pending_search_index_jobs

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="pub-fail")
        terminal = _enqueue_job(db_session, person.id, 3, key_suffix="pub-term")
        terminal.status = "COMPLETED"
        terminal.completed_at = datetime.now(UTC)
        db_session.add(terminal)
        db_session.commit()
        target = {job.id}
        original_reserve = SearchRepository.reserve_pending_jobs
        original_release = SearchRepository.release_reserved_job

        def reserve_only_target(self, *, limit: int = 50):
            jobs = original_reserve(self, limit=500)
            selected = [j for j in jobs if j.id in target][:limit]
            for j in jobs:
                if j.id not in target:
                    original_release(self, j.id)
            self.db.flush()
            return selected

        with (
            patch.object(SearchRepository, "reserve_pending_jobs", reserve_only_target),
            patch(
                "app.tasks.index_tasks.process_search_index_job.delay",
                side_effect=RuntimeError("broker down"),
            ),
        ):
            out = dispatch_pending_search_index_jobs(limit=10)

        assert out["reserved"] == 1
        assert out["published"] == 0
        assert out["restored"] == 1

        db_session.expire_all()
        row = db_session.get(SearchIndexJob, job.id)
        assert row is not None and row.status == "PENDING"
        assert row.started_at is None
        term = db_session.get(SearchIndexJob, terminal.id)
        assert term is not None and term.status == "COMPLETED"

        # Terminal job must not be reverted by release_reserved_job.
        assert SearchIndexService(db_session).release_reserved_job(terminal.id) is False
        db_session.expire_all()
        term2 = db_session.get(SearchIndexJob, terminal.id)
        assert term2 is not None and term2.status == "COMPLETED"
    finally:
        _cleanup_person(db_session, person.id)


def test_safe_error_sanitizes_dbapi_and_task_boundary(db_session) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.models.search import SearchIndexJob
    from app.modules.search.errors import (
        SearchIndexTaskError,
        safe_search_job_error,
    )
    from app.modules.search.service import SearchIndexService
    from app.tasks.index_tasks import process_search_index_job

    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        secret = "secret@example.com 홍길동 SEARCH-TEXT-SECRET"
        assert "secret@example.com" not in safe_search_job_error(
            SQLAlchemyError(secret)
        )
        assert "SEARCH-TEXT-SECRET" not in safe_search_job_error(RuntimeError(secret))

        job = _enqueue_job(db_session, person.id, 3, key_suffix="sanitize")
        assert SearchIndexService(db_session).repo.claim_pending_job(job.id) is not None
        db_session.commit()

        with patch.object(
            SearchIndexService,
            "_rebuild_person",
            side_effect=SQLAlchemyError(secret),
        ):
            with pytest.raises(SearchIndexTaskError) as raised:
                process_search_index_job(str(job.id))

        assert "secret@example.com" not in str(raised.value)
        assert "홍길동" not in str(raised.value)
        assert "SEARCH-TEXT-SECRET" not in str(raised.value)

        db_session.expire_all()
        row = db_session.get(SearchIndexJob, job.id)
        assert row is not None and row.status == "FAILED"
        err = row.error_message or ""
        assert err == "search index persistence failed"
        assert "secret@example.com" not in err
        assert "SEARCH-TEXT-SECRET" not in err
    finally:
        _cleanup_person(db_session, person.id)
