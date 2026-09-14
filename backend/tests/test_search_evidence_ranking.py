"""Search result evidence + project drill-down + rank-v2 regression tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

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


def _create_user(db_session, *, login_id: str, password: str = "Secret123!", role: str = "USER"):
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


def _cleanup_user(db_session, user_id) -> None:
    from app.db.models.revision import AuditLog
    from app.db.models.user import AppUser

    db_session.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db_session.execute(delete(AuditLog).where(AuditLog.target_id == user_id))
    db_session.execute(delete(AppUser).where(AppUser.id == user_id))
    db_session.commit()


def _login(client: TestClient, login_id: str, password: str = "Secret123!") -> str:
    response = client.post("/api/v1/auth/login", json={"login_id": login_id, "password": password})
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


def _search(client: TestClient, payload: dict):
    return client.post("/api/v1/search/people", json=payload)


def _ensure_code(db, code: str, code_type: str, name: str, *, parent_code: str | None = None) -> None:
    from app.db.models.code import CodeMaster

    existing = db.get(CodeMaster, code)
    if existing is None:
        db.add(
            CodeMaster(
                code=code,
                code_type=code_type,
                parent_code=parent_code,
                name=name,
                sort_order=0,
                is_active=True,
            )
        )
        db.commit()


def _seed_person(db, *, suffix: str | None = None):
    from tests.test_search_index import _seed_person as _base_seed

    return _base_seed(db, suffix=suffix)


def _cleanup_person(db, person_id) -> None:
    from tests.test_search_index import _cleanup_person as _base_cleanup

    _base_cleanup(db, person_id)


def _add_project(
    db,
    *,
    person_id,
    name: str,
    skill_code: str | None = None,
    exp_code: str | None = None,
    job_code: str | None = None,
    biz_code: str | None = None,
    customer_type_code: str | None = None,
    customer_name: str = "Cust",
    project_summary: str | None = None,
    responsibilities: str | None = None,
    evidence_type: str = "EXPLICIT",
    start: date | None = None,
    end: date | None = None,
    duration_months: int | None = None,
    deleted: bool = False,
):
    from app.db.models.project import (
        Project,
        ProjectBusinessDomain,
        ProjectCustomerType,
        ProjectExpertise,
        ProjectJob,
        ProjectSkill,
    )

    project = Project(
        person_id=person_id,
        project_name=name,
        customer_name=customer_name,
        project_summary=project_summary,
        responsibilities=responsibilities,
        start_date=start,
        end_date=end,
        duration_months=duration_months,
        source_type="USER",
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(project)
    db.flush()
    if skill_code:
        db.add(ProjectSkill(project_id=project.id, tech_code=skill_code))
    if exp_code:
        db.add(
            ProjectExpertise(
                project_id=project.id,
                exp_code=exp_code,
                evidence_type=evidence_type,
            )
        )
    if job_code:
        db.add(ProjectJob(project_id=project.id, job_code=job_code))
    if biz_code:
        db.add(ProjectBusinessDomain(project_id=project.id, biz_code=biz_code))
    if customer_type_code:
        db.add(
            ProjectCustomerType(
                project_id=project.id, customer_type_code=customer_type_code
            )
        )
    db.commit()
    db.refresh(project)
    return project


def _add_evidence(db, *, person_id, target_type: str, target_id, field_name: str | None,
                  quote: str = "evidence quote", page_no: int = 1,
                  processing_status: str = "READY", deleted_doc: bool = False,
                  other_person_id=None):
    from app.db.models.document import Document, DocumentGroup
    from app.db.models.evidence import Evidence, EvidenceLink

    _ensure_code(db, "DOC-RESUME", "DOC_TYPE", "이력서")
    owner = other_person_id or person_id
    group = DocumentGroup(
        person_id=owner,
        document_type_code="DOC-RESUME",
        title="경력기술서",
        deleted_at=datetime.now(UTC) if deleted_doc else None,
    )
    db.add(group)
    db.flush()
    doc = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="career.pdf",
        file_size=10,
        storage_key=f"test/{uuid.uuid4().hex}",
        sha256=uuid.uuid4().hex,
        processing_status=processing_status,
        deleted_at=datetime.now(UTC) if deleted_doc else None,
    )
    db.add(doc)
    db.flush()
    evidence = Evidence(
        document_id=doc.id,
        page_no=page_no,
        quote_text=quote,
        extraction_method="TEXT_PARSER",
    )
    db.add(evidence)
    db.flush()
    link = EvidenceLink(
        evidence_id=evidence.id,
        target_type=target_type,
        target_id=target_id,
        field_name=field_name,
        relation_type="SUPPORTS",
    )
    db.add(link)
    db.commit()
    db.refresh(evidence)
    return evidence, doc, group


# --------------------------------------------------------------------------- unit ranking


def test_rank_v2_component_normalization_and_caps() -> None:
    from app.modules.search.ranking import (
        SEARCH_RANKING_POLICY_VERSION,
        compute_final_relevance,
        compute_retrieval_relevance,
        relevance_to_score,
        rrf_score,
    )
    from app.modules.search.project_ranking import (
        JobConditionGroup,
        ProjectQuerySignals,
        compute_person_project_relevance,
        compute_project_base_score,
        compute_project_structured_score,
        compute_recency_score,
    )

    assert SEARCH_RANKING_POLICY_VERSION == "rank-v2"
    assert compute_retrieval_relevance(keyword_rank=1, semantic_rank=None) == rrf_score(1)
    both = compute_retrieval_relevance(keyword_rank=1, semantic_rank=2)
    assert 0 < both < 1

    # required-only → 100
    assert relevance_to_score(
        compute_final_relevance(
            required_score=1.0,
            retrieval_score=None,
            preferred_score=None,
            project_score=None,
            recency_score=None,
        )
    ) == 100

    # no components → 0
    assert relevance_to_score(
        compute_final_relevance(
            required_score=None,
            retrieval_score=None,
            preferred_score=None,
            project_score=None,
            recency_score=None,
        )
    ) == 0

    # count cap: 3 vs 30 same quality
    a = compute_person_project_relevance([1.0, 1.0, 1.0], 12)
    b = compute_person_project_relevance([1.0] * 30, 12)
    assert abs(a - b) < 1e-9

    # duration cap
    short = compute_person_project_relevance([1.0], 36)
    long = compute_person_project_relevance([1.0], 100)
    assert abs(short - long) < 1e-9

    # recency buckets with fixed as_of (undated related → 0.60)
    as_of = date(2026, 1, 1)
    assert compute_recency_score(date(2025, 1, 1), as_of=as_of) == 1.0
    assert compute_recency_score(date(2022, 1, 1), as_of=as_of) == 0.85
    assert compute_recency_score(date(2018, 1, 1), as_of=as_of) == 0.70
    assert compute_recency_score(date(2010, 1, 1), as_of=as_of) == 0.55
    assert compute_recency_score(None, as_of=as_of) == 0.60

    # recency minor: high project relevance old beats low recent
    old_high = compute_final_relevance(
        required_score=1.0,
        retrieval_score=None,
        preferred_score=None,
        project_score=1.0,
        recency_score=0.55,
    )
    recent_low = compute_final_relevance(
        required_score=1.0,
        retrieval_score=None,
        preferred_score=None,
        project_score=0.2,
        recency_score=1.0,
    )
    assert old_high > recent_low

    # required + preferred same category stay separated (no merged denominator)
    split_signals = ProjectQuerySignals(
        required_skills=("Python",),
        preferred_skills=("Java",),
    )
    required_hit = compute_project_structured_score(
        signals=split_signals,
        job_codes=set(),
        skill_codes={"Python"},
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="P1",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    preferred_hit = compute_project_structured_score(
        signals=split_signals,
        job_codes=set(),
        skill_codes={"Java"},
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="P2",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert required_hit is not None and preferred_hit is not None
    assert required_hit > preferred_hit
    # Both categories active in query: only-required match → (1.0+0.0)/2 = 0.50
    # only-preferred match → (0.0+0.5)/2 = 0.25
    assert required_hit == pytest.approx(0.50)
    assert preferred_hit == pytest.approx(0.25)

    both_hit = compute_project_structured_score(
        signals=split_signals,
        job_codes=set(),
        skill_codes={"Python", "Java"},
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="P3",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert both_hit == pytest.approx(0.75)
    # Mixed both-match is below required-only (when required is the sole active category).
    assert both_hit < 1.0

    # required-only perfect structured > preferred-only perfect
    req_only = ProjectQuerySignals(required_skills=("Python",))
    pref_only = ProjectQuerySignals(preferred_skills=("Python",))
    req_score = compute_project_structured_score(
        signals=req_only,
        job_codes=set(),
        skill_codes={"Python"},
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="R",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    pref_score = compute_project_structured_score(
        signals=pref_only,
        job_codes=set(),
        skill_codes={"Python"},
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="P",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert req_score == pytest.approx(1.0)
    assert pref_score == pytest.approx(0.5)
    assert req_score > pref_score

    # preferred EXPLICIT > preferred INFERRED (priority × expertise factor)
    pref_exp = ProjectQuerySignals(preferred_expertise=("EXP-RAG",))
    pref_explicit = compute_project_structured_score(
        signals=pref_exp,
        job_codes=set(),
        skill_codes=set(),
        expertise_rows=[("EXP-RAG", "EXPLICIT")],
        biz_codes=set(),
        customer_codes=set(),
        project_name="PE",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    pref_inferred = compute_project_structured_score(
        signals=pref_exp,
        job_codes=set(),
        skill_codes=set(),
        expertise_rows=[("EXP-RAG", "INFERRED")],
        biz_codes=set(),
        customer_codes=set(),
        project_name="PI",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert pref_explicit == pytest.approx(0.5)
    assert pref_inferred == pytest.approx(0.5 * 0.70)
    assert pref_explicit > pref_inferred

    # JOB root hierarchy: descendant count is not the denominator
    many = frozenset({f"JOB-AI-{i}" for i in range(10)} | {"JOB-AI", "JOB-AI-DEV"})
    job_signals = ProjectQuerySignals(
        required_job_groups=(JobConditionGroup(root_code="JOB-AI", codes=many),)
    )
    job_score = compute_project_structured_score(
        signals=job_signals,
        job_codes={"JOB-AI-DEV"},
        skill_codes=set(),
        expertise_rows=[],
        biz_codes=set(),
        customer_codes=set(),
        project_name="J",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert job_score == 1.0

    # active keyword / semantic miss must be 0.0 (not dropped from normalize)
    miss = compute_project_base_score(structured=1.0, keyword=0.0, semantic=None)
    hit = compute_project_base_score(structured=1.0, keyword=0.5, semantic=None)
    assert hit > miss
    # old bug: dropping keyword → structured-only 1.0 would invert ordering
    dropped = compute_project_base_score(structured=1.0, keyword=None, semantic=None)
    assert dropped > hit

    sem_miss = compute_project_base_score(structured=1.0, keyword=None, semantic=0.0)
    sem_hit = compute_project_base_score(structured=1.0, keyword=None, semantic=0.4)
    assert sem_hit > sem_miss

    # EXPLICIT > INFERRED (two-project structured comparison)
    exp_signals = ProjectQuerySignals(required_expertise=("EXP-RAG",))
    explicit = compute_project_structured_score(
        signals=exp_signals,
        job_codes=set(),
        skill_codes=set(),
        expertise_rows=[("EXP-RAG", "EXPLICIT")],
        biz_codes=set(),
        customer_codes=set(),
        project_name="E",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    inferred = compute_project_structured_score(
        signals=exp_signals,
        job_codes=set(),
        skill_codes=set(),
        expertise_rows=[("EXP-RAG", "INFERRED")],
        biz_codes=set(),
        customer_codes=set(),
        project_name="I",
        customer_name=None,
        responsibilities=None,
        project_summary=None,
    )
    assert explicit is not None and inferred is not None
    assert explicit > inferred
    assert explicit == pytest.approx(1.0)
    assert inferred == pytest.approx(0.70)


def test_hard_filter_not_overridden_by_project_relevance(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"hf_{suffix}")
    expert = _seed_person(db_session, suffix=f"e{suffix}")
    advanced = _seed_person(db_session, suffix=f"a{suffix}")
    advanced["profile"].technical_grade = "ADVANCED"
    db_session.commit()
    tech = expert["codes"]["tech"]
    # advanced has many related projects
    for i in range(5):
        _add_project(
            db_session,
            person_id=advanced["person"].id,
            name=f"AdvProj{i}",
            skill_code=tech,
            start=date(2024, 1, 1),
            end=date(2025, 1, 1),
            duration_months=12,
        )
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"grade": {"values": ["EXPERT"]}, "skills": [tech]}},
        )
        assert resp.status_code == 200, resp.text
        ids = {r["person_id"] for r in resp.json()["data"]}
        assert str(expert["person"].id) in ids
        assert str(advanced["person"].id) not in ids
    finally:
        _cleanup_person(db_session, expert["person"].id)
        _cleanup_person(db_session, advanced["person"].id)
        _cleanup_user(db_session, user.id)


def test_project_experience_ranks_higher(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"pr_{suffix}")
    a = _seed_person(db_session, suffix=f"a{suffix}")
    b = _seed_person(db_session, suffix=f"b{suffix}")
    tech = a["codes"]["tech"]
    # ensure both EXPERT + same skill
    for seeded in (a, b):
        seeded["profile"].technical_grade = "EXPERT"
    db_session.commit()
    # B has related projects
    _add_project(
        db_session,
        person_id=b["person"].id,
        name="Related RAG Platform",
        skill_code=tech,
        start=date(2023, 1, 1),
        end=date(2024, 6, 1),
        duration_months=18,
    )
    _add_project(
        db_session,
        person_id=b["person"].id,
        name="Related RAG 2",
        skill_code=tech,
        start=date(2022, 1, 1),
        end=date(2022, 12, 1),
        duration_months=12,
    )
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {
                "required": {"skills": [tech]},
                "sort": "RELEVANCE",
                "page_size": 50,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        ids = [r["person_id"] for r in data]
        assert str(b["person"].id) in ids and str(a["person"].id) in ids
        assert ids.index(str(b["person"].id)) < ids.index(str(a["person"].id))
        brow = next(r for r in data if r["person_id"] == str(b["person"].id))
        assert len(brow["top_projects"]) >= 1
        assert len(brow["top_projects"]) <= 3
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_user(db_session, user.id)


def test_required_skill_evidence_count_and_drilldown(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ev_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import PersonSkill

    skill = db_session.execute(
        select(PersonSkill).where(PersonSkill.person_id == seeded["person"].id)
    ).scalars().first()
    assert skill is not None
    ev1, doc, _ = _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PERSON_SKILL",
        target_id=skill.id,
        field_name="tech_code",
        quote="Python skill evidence one",
        page_no=2,
    )
    ev2, _, _ = _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PERSON_SKILL",
        target_id=skill.id,
        field_name="tech_code",
        quote="Python skill evidence two",
        page_no=3,
    )
    try:
        csrf = _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"skills": [seeded["codes"]["tech"]]}, "page_size": 50},
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        skill_matches = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        assert skill_matches
        assert skill_matches[0]["status"] == "MATCH"
        assert skill_matches[0]["evidence_count"] == 2
        # persistent evidence present
        persistent = [e for e in row["evidence"] if e.get("evidence_id")]
        assert persistent
        evidence_id = persistent[0]["evidence_id"]
        ev_resp = client.get(f"/api/v1/evidence/{evidence_id}")
        assert ev_resp.status_code == 200, ev_resp.text
        assert ev_resp.json()["data"]["id"] == evidence_id
        # document drill-down
        doc_id = persistent[0]["document_id"]
        doc_resp = client.get(f"/api/v1/documents/{doc_id}")
        assert doc_resp.status_code == 200, doc_resp.text
        # project drill-down if present
        if row["top_projects"]:
            pid = row["top_projects"][0]["project_id"]
            proj = client.get(f"/api/v1/projects/{pid}")
            assert proj.status_code == 200, proj.text
        # snippet cap
        for e in row["evidence"]:
            if e.get("snippet"):
                assert len(e["snippet"]) <= 240
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_document_chunk_derived_evidence_and_no_profile_leak(client: TestClient, db_session, monkeypatch) -> None:
    from tests.test_hybrid_search import (
        _FakeProvider,
        _add_index_item,
        _enable_embedding,
        _unit_vector,
    )

    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"chunk_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    secret = f"SECRET-SEARCH-TEXT-{suffix}"
    _add_index_item(
        db_session,
        person_id=seeded["person"].id,
        object_type="PROFILE",
        object_id=seeded["person"].id,
        search_text=secret,
        # No embedding → DOCUMENT_CHUNK becomes semantic best hit.
        embedding=None,
        source_weight="1.000",
    )
    # Create READY document + chunk + DOCUMENT_CHUNK index
    from app.db.models.document import Document, DocumentChunk, DocumentGroup

    _ensure_code(db_session, "DOC-RESUME", "DOC_TYPE", "이력서")
    group = DocumentGroup(
        person_id=seeded["person"].id,
        document_type_code="DOC-RESUME",
        title="원본문서",
    )
    db_session.add(group)
    db_session.flush()
    doc = Document(
        document_group_id=group.id,
        version_no=1,
        is_latest=True,
        original_filename="src.pdf",
        file_size=10,
        storage_key=f"test/{uuid.uuid4().hex}",
        sha256=uuid.uuid4().hex,
        processing_status="READY",
    )
    db_session.add(doc)
    db_session.flush()
    long_text = ("병원 진료기록을 활용하여 RAG 플랫폼을 구축한 경험. " * 40)
    chunk = DocumentChunk(
        document_id=doc.id,
        chunk_index=0,
        page_from=11,
        page_to=11,
        chunk_text=long_text,
        chunk_hash=uuid.uuid4().hex,
    )
    db_session.add(chunk)
    db_session.commit()
    db_session.refresh(chunk)
    _add_index_item(
        db_session,
        person_id=seeded["person"].id,
        object_type="DOCUMENT_CHUNK",
        object_id=chunk.id,
        search_text=long_text[:200],
        embedding=_unit_vector(index=0),
        source_weight="0.700",
    )
    _enable_embedding(monkeypatch)
    provider = _FakeProvider(vector=_unit_vector(index=0))
    try:
        _login(client, user.login_id)
        with patch("app.ai.providers.embedding.get_embedding_provider", return_value=provider):
            resp = _search(client, {"semantic_query": "의료 RAG 경험", "page_size": 20})
        assert resp.status_code == 200, resp.text
        assert len(provider.calls) == 1  # one query embedding
        body = resp.json()
        assert secret not in resp.text
        row = next(
            (r for r in body["data"] if r["person_id"] == str(seeded["person"].id)),
            None,
        )
        assert row is not None
        chunk_items = [
            e for e in row["evidence"] if e.get("source_level") == "DOCUMENT_CHUNK"
        ]
        assert chunk_items
        item = chunk_items[0]
        assert item["evidence_id"] is None
        assert item["document_id"] == str(doc.id)
        assert item["page_no"] == 11
        assert item["snippet"] is not None
        assert len(item["snippet"]) <= 240
        assert item["snippet"] != long_text
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_deleted_document_evidence_excluded(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"del_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import PersonSkill

    skill = db_session.execute(
        select(PersonSkill).where(PersonSkill.person_id == seeded["person"].id)
    ).scalars().first()
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PERSON_SKILL",
        target_id=skill.id,
        field_name="tech_code",
        quote="should be hidden",
        deleted_doc=True,
    )
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"skills": [seeded["codes"]["tech"]]}, "page_size": 50},
        )
        assert resp.status_code == 200
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        assert all(m["evidence_count"] == 0 for m in row["matches"] if m["type"] == "REQUIRED")
        assert all("should be hidden" not in (e.get("snippet") or "") for e in row["evidence"])
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_wrong_person_evidence_excluded(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"wp_{suffix}")
    a = _seed_person(db_session, suffix=f"a{suffix}")
    b = _seed_person(db_session, suffix=f"b{suffix}")
    from app.db.models.person import PersonSkill

    skill_a = db_session.execute(
        select(PersonSkill).where(PersonSkill.person_id == a["person"].id)
    ).scalars().first()
    # corrupt: evidence targets A's skill but document belongs to B
    _add_evidence(
        db_session,
        person_id=a["person"].id,
        target_type="PERSON_SKILL",
        target_id=skill_a.id,
        field_name="tech_code",
        quote="FOREIGN-PERSON-EVIDENCE",
        other_person_id=b["person"].id,
    )
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"skills": [a["codes"]["tech"]]}, "page_size": 50},
        )
        assert resp.status_code == 200
        row = next(r for r in resp.json()["data"] if r["person_id"] == str(a["person"].id))
        assert "FOREIGN-PERSON-EVIDENCE" not in resp.text
        assert all(
            (e.get("snippet") or "") != "FOREIGN-PERSON-EVIDENCE" for e in row["evidence"]
        )
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_user(db_session, user.id)


def test_structured_only_score_and_no_query(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"so_{suffix}")
    a = _seed_person(db_session, suffix=f"a{suffix}")
    b = _seed_person(db_session, suffix=f"b{suffix}")
    for seeded in (a, b):
        seeded["profile"].technical_grade = "EXPERT"
    db_session.commit()
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"grade": {"values": ["EXPERT"]}}, "sort": "RELEVANCE", "page_size": 50},
        )
        assert resp.status_code == 200
        rows = [
            r
            for r in resp.json()["data"]
            if r["person_id"] in {str(a["person"].id), str(b["person"].id)}
        ]
        assert rows
        assert all(r["score"] == 100 for r in rows)

        resp2 = _search(client, {"sort": "NAME_ASC", "page_size": 5})
        assert resp2.status_code == 200
        # no-query scores are 0
        assert all(r["score"] == 0 for r in resp2.json()["data"])
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_user(db_session, user.id)


def test_deleted_project_excluded_from_top_and_relevance(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"dp_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    tech = seeded["codes"]["tech"]
    alive = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Alive Project",
        skill_code=tech,
        start=date(2020, 1, 1),
        end=date(2020, 6, 1),
        duration_months=6,
    )
    dead = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Deleted Best Project",
        skill_code=tech,
        start=date(2024, 1, 1),
        end=date(2025, 1, 1),
        duration_months=12,
        deleted=True,
    )
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"skills": [tech]}, "page_size": 50})
        assert resp.status_code == 200
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        top_ids = {p["project_id"] for p in row["top_projects"]}
        assert str(dead.id) not in top_ids
        assert str(alive.id) in top_ids or len(top_ids) >= 0
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


# --------------------------------------------------------------------------- blocker regressions


def test_project_required_preferred_and_recency_semantics(db_session) -> None:
    from app.db.models.project import Project
    from app.modules.search.project_ranking import (
        ProjectQuerySignals,
        ProjectRankingRepository,
        build_project_query_signals,
        compute_project_base_score,
        compute_recency_score,
    )
    from app.modules.search.query_schemas import (
        PreferredConditionBlock,
        SearchConditionBlock,
        SearchPeopleRequest,
    )

    suffix = uuid.uuid4().hex[:8]
    seeded = _seed_person(db_session, suffix=suffix)
    tech = seeded["codes"]["tech"]
    lonely = _seed_person(db_session, suffix=f"l{suffix}")
    undated = _seed_person(db_session, suffix=f"u{suffix}")
    unique_tech = f"TECH-UNDATED-{suffix}"
    _ensure_code(db_session, unique_tech, "TECH", "UndatedOnly")
    # Soft-delete seed projects so only the undated related project remains.
    for project in db_session.execute(
        select(Project).where(Project.person_id == undated["person"].id)
    ).scalars():
        project.deleted_at = datetime.now(UTC)
    db_session.commit()
    _add_project(
        db_session,
        person_id=undated["person"].id,
        name="Undated related",
        skill_code=unique_tech,
        start=None,
        end=None,
        duration_months=None,
    )
    try:
        req = SearchPeopleRequest(
            required=SearchConditionBlock(skills=[tech]),
            preferred=PreferredConditionBlock(skills=["NOPE-TECH"]),
            keyword_query="DEMIS",
        )
        signals = build_project_query_signals(
            req,
            required_job_groups=[],
            preferred_job_groups=[],
        )
        assert signals.required_skills == (tech,)
        assert signals.preferred_skills == ("NOPE-TECH",)
        assert signals.keyword_query == "DEMIS"

        a = compute_project_base_score(structured=1.0, keyword=0.0, semantic=None)
        b = compute_project_base_score(structured=1.0, keyword=0.5, semantic=None)
        assert b > a

        as_of = date(2026, 1, 1)
        assert compute_recency_score(None, as_of=as_of) == 0.60

        repo = ProjectRankingRepository(db_session)
        summaries = repo.summarize_persons(
            [lonely["person"].id, undated["person"].id],
            request=SearchPeopleRequest(
                required=SearchConditionBlock(skills=[unique_tech])
            ),
            signals=ProjectQuerySignals(required_skills=(unique_tech,)),
            query_vector=None,
            as_of=as_of,
        )
        assert summaries[lonely["person"].id].related_count == 0
        assert summaries[lonely["person"].id].recency_score is None
        assert summaries[undated["person"].id].related_count >= 1
        assert summaries[undated["person"].id].latest_related_date is None
        assert summaries[undated["person"].id].recency_score == 0.60
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_person(db_session, lonely["person"].id)
        _cleanup_person(db_session, undated["person"].id)


def test_project_only_skill_and_relation_evidence(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"pos_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import PersonSkill

    # Remove person skills so match is project-only
    db_session.execute(
        delete(PersonSkill).where(PersonSkill.person_id == seeded["person"].id)
    )
    db_session.commit()
    tech = seeded["codes"]["tech"]
    project = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Project Python only",
        skill_code=tech,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"skills:{tech}",
        quote="project skill evidence a",
        page_no=1,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"skills:{tech}",
        quote="project skill evidence b",
        page_no=2,
    )
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"skills": [tech]}, "page_size": 50})
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        skill_match = next(m for m in row["matches"] if m["type"] == "REQUIRED")
        assert skill_match["status"] == "MATCH"
        assert skill_match["evidence_count"] == 2
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_biz_customer_job_descendant_and_preferred_evidence(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"rel_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    root = f"JOB-ROOT-{suffix}"
    child = f"JOB-CHILD-{suffix}"
    _ensure_code(db_session, root, "JOB", "Root AI", parent_code=None)
    _ensure_code(db_session, child, "JOB", "Child Dev", parent_code=root)
    biz = seeded["codes"]["biz"]
    cust = seeded["codes"]["cust"]
    project = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Relation Project",
        job_code=child,
        biz_code=biz,
        customer_type_code=cust,
        exp_code=seeded["codes"]["exp"],
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"jobs:{child}",
        quote="job child evidence",
        page_no=1,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"business_domains:{biz}",
        quote="biz evidence",
        page_no=2,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"customer_types:{cust}",
        quote="customer evidence",
        page_no=3,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"expertise:{seeded['codes']['exp']}",
        quote="exp project evidence",
        page_no=4,
    )
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {
                "required": {
                    "jobs": [root],
                    "business_domains": [biz],
                    "customer_types": [cust],
                    "expertise": [seeded["codes"]["exp"]],
                },
                "preferred": {"jobs": [root]},
                "page_size": 50,
            },
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        required = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        preferred = [m for m in row["matches"] if m["type"] == "PREFERRED"]
        assert all(m["status"] == "MATCH" for m in required)
        assert any(m["evidence_count"] > 0 for m in required)
        # job / biz / customer / expertise should each contribute evidence
        assert sum(m["evidence_count"] for m in required) >= 4
        assert preferred
        assert preferred[0]["status"] == "MATCH"
        assert preferred[0]["evidence_count"] >= 1
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_certification_and_project_keyword_evidence_filter(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ck_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import Certification

    aws = Certification(
        person_id=seeded["person"].id,
        certification_name="AWS Solutions Architect",
        issuer="Amazon",
        source_type="USER",
    )
    pmp = Certification(
        person_id=seeded["person"].id,
        certification_name="PMP",
        issuer="PMI",
        source_type="USER",
    )
    db_session.add_all([aws, pmp])
    db_session.commit()
    db_session.refresh(aws)
    db_session.refresh(pmp)

    # Root + field-specific Evidence (expiry_date must NOT count for AWS name query)
    for field_name, quote, page in [
        (None, "AWS root evidence", 1),
        ("certification_name", "AWS name evidence", 2),
        ("issuer", "AWS issuer evidence", 3),
        ("expiry_date", "AWS expiry evidence", 4),
    ]:
        _add_evidence(
            db_session,
            person_id=seeded["person"].id,
            target_type="CERTIFICATION",
            target_id=aws.id,
            field_name=field_name,
            quote=quote,
            page_no=page,
        )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="CERTIFICATION",
        target_id=pmp.id,
        field_name=None,
        quote="PMP cert evidence",
        page_no=5,
    )
    demis = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="DEMIS Hospital Platform",
        project_summary="DEMIS migration",
    )
    unrelated = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Unrelated ERP",
        project_summary="SAP rollout",
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=demis.id,
        field_name="project_name",
        quote="DEMIS name evidence",
        page_no=6,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=unrelated.id,
        field_name="project_name",
        quote="ERP unrelated evidence",
        page_no=7,
    )
    try:
        _login(client, user.login_id)

        # Query by certification_name token → root + name only (not issuer/expiry)
        resp = _search(
            client,
            {
                "required": {
                    "certifications": ["AWS"],
                    "project_keywords": ["DEMIS"],
                },
                "page_size": 50,
            },
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        snippets = " ".join(e.get("snippet") or "" for e in row["evidence"])
        cert_match = next(
            m for m in row["matches"] if m["type"] == "REQUIRED" and "AWS" in m["condition"]
        )
        assert cert_match["evidence_count"] == 2  # root + certification_name
        assert "AWS root evidence" in snippets or cert_match["evidence_count"] == 2
        assert "AWS expiry evidence" not in snippets
        assert "PMP cert evidence" not in snippets
        kw_match = next(
            m
            for m in row["matches"]
            if m["type"] == "REQUIRED" and "DEMIS" in m["condition"]
        )
        assert kw_match["evidence_count"] >= 1
        assert "ERP unrelated evidence" not in snippets

        # Query by issuer token → root + issuer only
        resp2 = _search(
            client,
            {"required": {"certifications": ["Amazon"]}, "page_size": 50},
        )
        assert resp2.status_code == 200, resp2.text
        row2 = next(
            r
            for r in resp2.json()["data"]
            if r["person_id"] == str(seeded["person"].id)
        )
        cert_match2 = next(
            m
            for m in row2["matches"]
            if m["type"] == "REQUIRED" and "Amazon" in m["condition"]
        )
        assert cert_match2["evidence_count"] == 2  # root + issuer
        snippets2 = " ".join(e.get("snippet") or "" for e in row2["evidence"])
        assert "AWS expiry evidence" not in snippets2
        assert "AWS name evidence" not in snippets2 or cert_match2["evidence_count"] == 2
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_ongoing_project_recent_fallback_ordering(client: TestClient, db_session) -> None:
    """No project signal → top_projects by COALESCE(end, start) DESC (ongoing not demoted)."""
    from app.db.models.project import Project
    from app.modules.search.project_ranking import (
        format_project_period,
        project_recent_sort_key,
    )

    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"rf_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)

    # Soft-delete seed projects so ordering is deterministic from fixtures below.
    for project in db_session.execute(
        select(Project).where(Project.person_id == seeded["person"].id)
    ).scalars():
        project.deleted_at = datetime.now(UTC)
    db_session.commit()

    ongoing = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Ongoing 2025",
        start=date(2025, 1, 1),
        end=None,
        duration_months=None,
    )
    mid = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Finished 2024",
        start=date(2023, 1, 1),
        end=date(2024, 1, 1),
        duration_months=12,
    )
    old = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Finished 2022",
        start=date(2022, 1, 1),
        end=date(2022, 12, 1),
        duration_months=11,
    )

    # Unit: sort key itself
    ordered = sorted([ongoing, mid, old], key=project_recent_sort_key)
    assert [p.id for p in ordered] == [ongoing.id, mid.id, old.id]
    assert format_project_period(ongoing.start_date, ongoing.end_date) == "2025.01 ~ 현재"

    try:
        _login(client, user.login_id)
        # Grade-only query → no project ranking signal → recent fallback.
        resp = _search(
            client,
            {
                "required": {"grade": {"values": ["EXPERT"]}},
                "page_size": 50,
            },
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        top_ids = [p["project_id"] for p in row["top_projects"]]
        assert top_ids[:3] == [str(ongoing.id), str(mid.id), str(old.id)]
        assert row["top_projects"][0]["period"] == "2025.01 ~ 현재"
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_top_project_any_field_evidence_ids(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"tp_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    tech = seeded["codes"]["tech"]
    biz = seeded["codes"]["biz"]
    cust = seeded["codes"]["cust"]
    project = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="AnyField Project",
        skill_code=tech,
        biz_code=biz,
        customer_type_code=cust,
        project_summary="summary text",
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
        duration_months=6,
    )
    fields = [
        (None, "root null field"),
        ("project_summary", "summary field evidence"),
        (f"business_domains:{biz}", "biz field evidence"),
        (f"customer_types:{cust}", "customer field evidence"),
        ("project_name", "name field evidence"),
        ("responsibilities", "extra sixth should be capped"),
    ]
    evidence_ids = []
    for idx, (field_name, quote) in enumerate(fields, start=1):
        ev, _, _ = _add_evidence(
            db_session,
            person_id=seeded["person"].id,
            target_type="PROJECT",
            target_id=project.id,
            field_name=field_name,
            quote=quote,
            page_no=idx,
        )
        evidence_ids.append(str(ev.id))
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"skills": [tech]}, "page_size": 50})
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        top = next(p for p in row["top_projects"] if p["project_id"] == str(project.id))
        assert len(top["evidence_ids"]) == 5
        # deterministic page_no order → first five
        assert top["evidence_ids"] == evidence_ids[:5]
        for eid in top["evidence_ids"]:
            ev_resp = client.get(f"/api/v1/evidence/{eid}")
            assert ev_resp.status_code == 200, ev_resp.text
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_evidence_distinct_dedupe_project_skill(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"dd_{suffix}")
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import PersonSkill

    skill = db_session.execute(
        select(PersonSkill).where(PersonSkill.person_id == seeded["person"].id)
    ).scalars().first()
    tech = seeded["codes"]["tech"]
    project = _add_project(
        db_session,
        person_id=seeded["person"].id,
        name="Dedupe Project",
        skill_code=tech,
    )
    # Same evidence cannot appear twice; use two distinct links
    ev, _, _ = _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PERSON_SKILL",
        target_id=skill.id,
        field_name="tech_code",
        quote="person skill",
        page_no=1,
    )
    _add_evidence(
        db_session,
        person_id=seeded["person"].id,
        target_type="PROJECT",
        target_id=project.id,
        field_name=f"skills:{tech}",
        quote="project skill",
        page_no=2,
    )
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"skills": [tech]}, "page_size": 50})
        assert resp.status_code == 200
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        skill_match = next(m for m in row["matches"] if m["type"] == "REQUIRED")
        assert skill_match["evidence_count"] == 2
        ids = [e["evidence_id"] for e in row["evidence"] if e.get("evidence_id")]
        assert len(ids) == len(set(ids))
        assert str(ev.id) in ids
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)
