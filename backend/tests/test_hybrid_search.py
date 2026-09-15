"""Hybrid search (structured + FTS/trgm + pgvector) tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

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


def _create_user(db_session, *, login_id: str, password: str, role: str = "USER"):
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
    response = client.post(
        "/api/v1/auth/login",
        json={"login_id": login_id, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("ts_csrf")
    assert csrf
    return csrf


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
    elif parent_code is not None and existing.parent_code != parent_code:
        existing.parent_code = parent_code
        db.commit()


def _cleanup_person(db, person_id) -> None:
    from tests.test_search_index import _cleanup_person as _base_cleanup

    _base_cleanup(db, person_id)


def _seed_person(db, *, suffix: str | None = None):
    from tests.test_search_index import _seed_person as _base_seed

    return _base_seed(db, suffix=suffix)


def _vector(dim: int = 1024, fill: float = 0.01) -> list[float]:
    return [float(fill)] * dim


def _unit_vector(dim: int = 1024, index: int = 0, value: float = 1.0) -> list[float]:
    vec = [0.0] * dim
    vec[index % dim] = float(value)
    return vec


class _FakeProvider:
    def __init__(self, vector: list[float] | None = None, *, error: Exception | None = None):
        self.vector = vector if vector is not None else _vector()
        self.error = error
        self.calls: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return list(self.vector)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


def _enable_embedding(monkeypatch, *, max_chars: int = 8000, model: str = "bge-m3"):
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL", model)
    monkeypatch.setenv("EMBEDDING_MAX_INPUT_CHARS", str(max_chars))
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1024")
    get_settings.cache_clear()
    return get_settings()


def _disable_embedding(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_ENABLED", "false")
    get_settings.cache_clear()
    return get_settings()


def _add_index_item(
    db,
    *,
    person_id,
    object_type: str,
    object_id,
    search_text: str,
    source_weight: str = "1.000",
    embedding: list[float] | None = None,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
    is_active: bool = True,
):
    from app.db.models.search import SearchIndexItem
    from app.modules.search.embedding_policy import (
        current_embedding_model,
        effective_embedding_version,
    )

    item = SearchIndexItem(
        person_id=person_id,
        object_type=object_type,
        object_id=object_id,
        search_text=search_text,
        source_weight=Decimal(source_weight),
        metadata_json={},
        embedding=embedding,
        embedding_model=embedding_model
        if embedding_model is not None
        else (current_embedding_model() if embedding is not None else None),
        embedding_version=embedding_version
        if embedding_version is not None
        else (effective_embedding_version() if embedding is not None else None),
        is_active=is_active,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _search(client: TestClient, payload: dict, *, csrf: str | None = None):
    headers = {}
    # CSRF must NOT be required; callers may pass csrf to prove it is ignored.
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return client.post("/api/v1/search/people", json=payload, headers=headers)


def _person_ids(resp_json: dict) -> list[str]:
    return [row["person_id"] for row in resp_json["data"]]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_search_requires_auth(client: TestClient) -> None:
    resp = client.post("/api/v1/search/people", json={})
    assert resp.status_code == 401


def test_search_user_and_admin_no_csrf(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"u_{suffix}", password="Secret123!", role="USER")
    admin = _create_user(db_session, login_id=f"a_{suffix}", password="Secret123!", role="ADMIN")
    seeded = _seed_person(db_session, suffix=suffix)
    try:
        _login(client, user.login_id)
        # No CSRF header
        resp = client.post(
            "/api/v1/search/people",
            json={"required": {"grade": {"values": ["EXPERT"]}}, "page": 1, "page_size": 10},
        )
        assert resp.status_code == 200, resp.text
        assert "data" in resp.json()

        client.cookies.clear()
        csrf = _login(client, admin.login_id)
        resp2 = _search(
            client,
            {"required": {"grade": {"values": ["EXPERT"]}}},
            csrf=csrf,  # present but not required
        )
        assert resp2.status_code == 200, resp2.text
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)
        _cleanup_user(db_session, admin.id)


# ---------------------------------------------------------------------------
# Keyword / FTS / trigram
# ---------------------------------------------------------------------------


def test_keyword_only_and_fts(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"kw_{suffix}", password="Secret123!")
    a = _seed_person(db_session, suffix=f"a{suffix}")
    b = _seed_person(db_session, suffix=f"b{suffix}")
    unique_token = f"ZxqUniqueToken{suffix}"
    _add_index_item(
        db_session,
        person_id=a["person"].id,
        object_type="PROFILE",
        object_id=a["person"].id,
        search_text=f"profile {unique_token} python rag",
    )
    _add_index_item(
        db_session,
        person_id=b["person"].id,
        object_type="PROFILE",
        object_id=b["person"].id,
        search_text="profile ordinary backend java",
    )
    try:
        _login(client, user.login_id)
        resp = _search(client, {"keyword_query": unique_token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids = _person_ids(body)
        assert str(a["person"].id) in ids
        assert str(b["person"].id) not in ids
        row = body["data"][0]
        assert "search_text" not in row
        assert "embedding" not in row
        assert "phone" not in row.get("person", {})
        assert "email" not in row.get("person", {})
        assert isinstance(row["evidence"], list)
        assert isinstance(row["top_projects"], list)
        assert len(row["top_projects"]) <= 3
        # PROFILE search_text must never leak into response snippets.
        assert unique_token not in str(row.get("evidence"))
        assert body["relaxations"] == []
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_user(db_session, user.id)


def test_trigram_fuzzy_keyword(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"tr_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    # Distinctive token long enough for trigram; query is a near-miss (no ILIKE).
    phrase = f"ZxqFastAPIFramework{suffix}"
    # One character substitution mid-token → ILIKE fails, trigram should still hit.
    fuzzy = phrase[:10] + "X" + phrase[11:]
    assert fuzzy != phrase
    _add_index_item(
        db_session,
        person_id=seeded["person"].id,
        object_type="PROJECT",
        object_id=seeded["project_a"].id,
        # Keep search_text close to the query so pg_trgm similarity clears threshold
        # (channel compares full search_text, not best substring).
        search_text=phrase,
    )
    # Sanity: similarity should clear the shared threshold for this pair.
    from app.modules.search.ranking import KEYWORD_TRIGRAM_THRESHOLD

    sim = db_session.execute(
        text("SELECT similarity(:a, :b)"),
        {"a": phrase, "b": fuzzy},
    ).scalar()
    assert float(sim) >= KEYWORD_TRIGRAM_THRESHOLD
    # Exact ILIKE must fail so we are exercising trigram (or FTS), not substring.
    assert fuzzy.lower() not in phrase.lower()
    try:
        _login(client, user.login_id)
        resp = _search(client, {"keyword_query": fuzzy})
        assert resp.status_code == 200, resp.text
        assert str(seeded["person"].id) in _person_ids(resp.json())
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_weird_keyword_syntax_does_not_500(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ws_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    _add_index_item(
        db_session,
        person_id=seeded["person"].id,
        object_type="PROFILE",
        object_id=seeded["person"].id,
        search_text="python OR postgres",
    )
    try:
        _login(client, user.login_id)
        for q in [
            "python OR (",
            ")))",
            "a & b | c",
            '"unclosed',
            "   ",
        ]:
            payload = {"keyword_query": q} if q.strip() else {}
            resp = _search(client, payload)
            assert resp.status_code in (200, 422), (q, resp.text)
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


# ---------------------------------------------------------------------------
# Hard filters
# ---------------------------------------------------------------------------


def test_hard_filter_grade_and_cross_field_and(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"hf_{suffix}", password="Secret123!")
    expert = _seed_person(db_session, suffix=f"e{suffix}")
    beginner = _seed_person(db_session, suffix=f"b{suffix}")
    beginner["profile"].technical_grade = "BEGINNER"
    db_session.commit()
    try:
        _login(client, user.login_id)
        codes = expert["codes"]
        resp = _search(
            client,
            {
                "required": {
                    "grade": {"values": ["EXPERT"]},
                    "skills": [codes["tech"]],
                }
            },
        )
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(expert["person"].id) in ids
        assert str(beginner["person"].id) not in ids
    finally:
        _cleanup_person(db_session, expert["person"].id)
        _cleanup_person(db_session, beginner["person"].id)
        _cleanup_user(db_session, user.id)


def test_same_field_or_skills(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"or_{suffix}", password="Secret123!")
    py = _seed_person(db_session, suffix=f"py{suffix}")
    other = _seed_person(db_session, suffix=f"ot{suffix}")
    # other keeps FastAPI skill (tech2); remove Python if present — already has both.
    # Create a third person with neither matching shared codes — use unique tech.
    codes_py = py["codes"]
    # Give `other` only a skill that is in OR list via tech2 shared? Use py's tech2 on other.
    # Simpler: search skills = [py.tech, unique_other] ANY → both match if other has unique.
    unique_tech = f"TECH-UNIQ-{suffix}"
    _ensure_code(db_session, unique_tech, "TECH", "UniqueTech")
    from app.db.models.person import PersonSkill

    db_session.execute(
        delete(PersonSkill).where(PersonSkill.person_id == other["person"].id)
    )
    db_session.add(
        PersonSkill(
            person_id=other["person"].id,
            tech_code=unique_tech,
            is_representative=True,
            source_type="USER",
        )
    )
    db_session.commit()
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {
                "required": {"skills": [codes_py["tech"], unique_tech]},
                "skill_match_mode": "ANY",
            },
        )
        assert resp.status_code == 200, resp.text
        ids = set(_person_ids(resp.json()))
        assert str(py["person"].id) in ids
        assert str(other["person"].id) in ids
    finally:
        _cleanup_person(db_session, py["person"].id)
        _cleanup_person(db_session, other["person"].id)
        _cleanup_user(db_session, user.id)


def test_skill_all_across_person_and_project(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"all_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    t1 = f"TECH-A-{suffix}"
    t2 = f"TECH-B-{suffix}"
    _ensure_code(db_session, t1, "TECH", "TechA")
    _ensure_code(db_session, t2, "TECH", "TechB")
    from app.db.models.person import PersonSkill
    from app.db.models.project import ProjectSkill

    db_session.execute(
        delete(PersonSkill).where(PersonSkill.person_id == seeded["person"].id)
    )
    db_session.execute(
        delete(ProjectSkill).where(ProjectSkill.project_id == seeded["project_a"].id)
    )
    db_session.add(
        PersonSkill(
            person_id=seeded["person"].id,
            tech_code=t1,
            is_representative=True,
            source_type="USER",
        )
    )
    db_session.add(ProjectSkill(project_id=seeded["project_a"].id, tech_code=t2))
    db_session.commit()

    missing = _seed_person(db_session, suffix=f"m{suffix}")
    db_session.execute(
        delete(PersonSkill).where(PersonSkill.person_id == missing["person"].id)
    )
    db_session.add(
        PersonSkill(
            person_id=missing["person"].id,
            tech_code=t1,
            is_representative=True,
            source_type="USER",
        )
    )
    db_session.commit()
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"skills": [t1, t2]}, "skill_match_mode": "ALL"},
        )
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(seeded["person"].id) in ids
        assert str(missing["person"].id) not in ids
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_person(db_session, missing["person"].id)
        _cleanup_user(db_session, user.id)


def test_job_hierarchy_expansion(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"jh_{suffix}", password="Secret123!")
    parent = f"JOB-PARENT-{suffix}"
    child = f"JOB-CHILD-{suffix}"
    _ensure_code(db_session, parent, "JOB", "Parent Job")
    _ensure_code(db_session, child, "JOB", "Child Job", parent_code=parent)
    seeded = _seed_person(db_session, suffix=suffix)
    from app.db.models.person import PersonJob

    db_session.execute(delete(PersonJob).where(PersonJob.person_id == seeded["person"].id))
    db_session.add(
        PersonJob(
            person_id=seeded["person"].id,
            job_code=child,
            job_type="PRIMARY",
            sort_order=0,
            source_type="USER",
            is_active=True,
        )
    )
    db_session.commit()
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"jobs": [parent]}})
        assert resp.status_code == 200, resp.text
        assert str(seeded["person"].id) in _person_ids(resp.json())
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_career_coalesce_confirmed_over_calculated(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"cr_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    profile = seeded["profile"]
    profile.career_confirmed_months = 60
    profile.career_calculated_months = 200
    db_session.commit()
    null_career = _seed_person(db_session, suffix=f"n{suffix}")
    null_career["profile"].career_confirmed_months = None
    null_career["profile"].career_calculated_months = None
    db_session.commit()
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"career": {"min_months": 50, "max_months": 70}}},
        )
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(seeded["person"].id) in ids
        assert str(null_career["person"].id) not in ids
        # Response career_months uses coalesce
        row = next(r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id))
        assert row["person"]["career_months"] == 60
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_person(db_session, null_career["person"].id)
        _cleanup_user(db_session, user.id)


# ---------------------------------------------------------------------------
# Semantic / embedding
# ---------------------------------------------------------------------------


def test_semantic_fake_provider_and_version_filter(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _enable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"sem_{suffix}", password="Secret123!")
    good = _seed_person(db_session, suffix=f"g{suffix}")
    stale = _seed_person(db_session, suffix=f"s{suffix}")
    query_vec = _unit_vector(index=0)
    _add_index_item(
        db_session,
        person_id=good["person"].id,
        object_type="PROFILE",
        object_id=good["person"].id,
        search_text="semantic good",
        embedding=query_vec,
    )
    _add_index_item(
        db_session,
        person_id=stale["person"].id,
        object_type="PROFILE",
        object_id=stale["person"].id,
        search_text="semantic stale",
        embedding=query_vec,
        embedding_version="stale-version",
    )
    fake = _FakeProvider(vector=query_vec)
    try:
        _login(client, user.login_id)
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            resp = _search(client, {"semantic_query": "LLM RAG experience"})
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(good["person"].id) in ids
        assert str(stale["person"].id) not in ids
        assert fake.calls
    finally:
        _cleanup_person(db_session, good["person"].id)
        _cleanup_person(db_session, stale["person"].id)
        _cleanup_user(db_session, user.id)


def test_document_chunk_source_weight_and_doc_rich_not_dominating(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _enable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"dc_{suffix}", password="Secret123!")
    profile_person = _seed_person(db_session, suffix=f"p{suffix}")
    doc_rich = _seed_person(db_session, suffix=f"d{suffix}")
    query_vec = _unit_vector(index=3)
    near = _unit_vector(index=3)
    far = _unit_vector(index=7)

    _add_index_item(
        db_session,
        person_id=profile_person["person"].id,
        object_type="PROFILE",
        object_id=profile_person["person"].id,
        search_text="profile match",
        source_weight="1.000",
        embedding=near,
    )
    # Many DOCUMENT_CHUNK hits with lower weight / farther vector should not dominate
    for i in range(8):
        _add_index_item(
            db_session,
            person_id=doc_rich["person"].id,
            object_type="DOCUMENT_CHUNK",
            object_id=uuid.uuid4(),
            search_text=f"chunk {i} noise",
            source_weight="0.700",
            embedding=far if i < 7 else near,
        )
    fake = _FakeProvider(vector=query_vec)
    try:
        _login(client, user.login_id)
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            resp = _search(client, {"semantic_query": "profile semantic", "sort": "RELEVANCE"})
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(profile_person["person"].id) in ids
        # Profile person should rank at or above doc-rich (higher effective weight)
        if str(doc_rich["person"].id) in ids:
            assert ids.index(str(profile_person["person"].id)) <= ids.index(
                str(doc_rich["person"].id)
            )
    finally:
        _cleanup_person(db_session, profile_person["person"].id)
        _cleanup_person(db_session, doc_rich["person"].id)
        _cleanup_user(db_session, user.id)


def test_hybrid_merge_and_rrf_deterministic(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _enable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"hy_{suffix}", password="Secret123!")
    a = _seed_person(db_session, suffix=f"a{suffix}")
    b = _seed_person(db_session, suffix=f"b{suffix}")
    token = f"HybridKey{suffix}"
    _add_index_item(
        db_session,
        person_id=a["person"].id,
        object_type="PROFILE",
        object_id=a["person"].id,
        search_text=f"{token} alpha",
        embedding=_unit_vector(index=1),
    )
    _add_index_item(
        db_session,
        person_id=b["person"].id,
        object_type="PROFILE",
        object_id=b["person"].id,
        search_text="beta unrelated",
        embedding=_unit_vector(index=0),
    )
    fake = _FakeProvider(vector=_unit_vector(index=0))
    try:
        _login(client, user.login_id)
        payload = {
            "keyword_query": token,
            "semantic_query": "semantic beta",
            "sort": "RELEVANCE",
        }
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            r1 = _search(client, payload)
            r2 = _search(client, payload)
        assert r1.status_code == 200 and r2.status_code == 200
        assert _person_ids(r1.json()) == _person_ids(r2.json())
        assert [x["score"] for x in r1.json()["data"]] == [
            x["score"] for x in r2.json()["data"]
        ]
        ids = set(_person_ids(r1.json()))
        assert str(a["person"].id) in ids
        assert str(b["person"].id) in ids
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_user(db_session, user.id)


def test_structured_only_with_embedding_disabled(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _disable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"st_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"grade": {"values": ["EXPERT"]}}, "sort": "NAME_ASC"},
        )
        assert resp.status_code == 200, resp.text
        assert str(seeded["person"].id) in _person_ids(resp.json())
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_semantic_with_embedding_disabled_returns_503(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _disable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"off_{suffix}", password="Secret123!")
    try:
        _login(client, user.login_id)
        resp = _search(client, {"semantic_query": "anything"})
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("code") == "SEARCH_EMBEDDING_UNAVAILABLE"
    finally:
        _cleanup_user(db_session, user.id)


def test_provider_failure_sanitized_503(
    client: TestClient, db_session, monkeypatch
) -> None:
    suffix = uuid.uuid4().hex[:8]
    _enable_embedding(monkeypatch)
    user = _create_user(db_session, login_id=f"pf_{suffix}", password="Secret123!")
    from app.ai.providers.errors import AIProviderError

    fake = _FakeProvider(error=AIProviderError("secret url https://internal/v1 leak"))
    try:
        _login(client, user.login_id)
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            resp = _search(client, {"semantic_query": "query"})
        assert resp.status_code == 503
        text_body = resp.text
        assert "https://internal" not in text_body
        assert "secret" not in text_body.lower() or "SEARCH_EMBEDDING" in text_body
        assert resp.json().get("code") == "SEARCH_EMBEDDING_UNAVAILABLE"
    finally:
        _cleanup_user(db_session, user.id)


# ---------------------------------------------------------------------------
# Eligibility / index flags / pagination
# ---------------------------------------------------------------------------


def test_deleted_person_and_inactive_index_excluded(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ex_{suffix}", password="Secret123!")
    active = _seed_person(db_session, suffix=f"a{suffix}")
    deleted = _seed_person(db_session, suffix=f"d{suffix}")
    inactive_idx = _seed_person(db_session, suffix=f"i{suffix}")

    deleted["person"].status = "DELETED"
    deleted["person"].deleted_at = datetime.now(UTC)
    db_session.commit()

    token = f"ExclToken{suffix}"
    _add_index_item(
        db_session,
        person_id=active["person"].id,
        object_type="PROFILE",
        object_id=active["person"].id,
        search_text=token,
        is_active=True,
    )
    _add_index_item(
        db_session,
        person_id=deleted["person"].id,
        object_type="PROFILE",
        object_id=deleted["person"].id,
        search_text=token,
        is_active=True,
    )
    _add_index_item(
        db_session,
        person_id=inactive_idx["person"].id,
        object_type="PROFILE",
        object_id=inactive_idx["person"].id,
        search_text=token,
        is_active=False,
    )
    try:
        _login(client, user.login_id)
        resp = _search(client, {"keyword_query": token})
        assert resp.status_code == 200, resp.text
        ids = _person_ids(resp.json())
        assert str(active["person"].id) in ids
        assert str(deleted["person"].id) not in ids
        assert str(inactive_idx["person"].id) not in ids
    finally:
        _cleanup_person(db_session, active["person"].id)
        _cleanup_person(db_session, deleted["person"].id)
        _cleanup_person(db_session, inactive_idx["person"].id)
        _cleanup_user(db_session, user.id)


def test_pagination_stability(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"pg_{suffix}", password="Secret123!")
    people = []
    for i in range(5):
        seeded = _seed_person(db_session, suffix=f"{i}{suffix}")
        seeded["profile"].name = f"PagePerson_{i}_{suffix}"
        db_session.commit()
        people.append(seeded)
    try:
        _login(client, user.login_id)
        p1 = _search(
            client,
            {
                "required": {"grade": {"values": ["EXPERT"]}},
                "sort": "NAME_ASC",
                "page": 1,
                "page_size": 2,
            },
        )
        p2 = _search(
            client,
            {
                "required": {"grade": {"values": ["EXPERT"]}},
                "sort": "NAME_ASC",
                "page": 2,
                "page_size": 2,
            },
        )
        assert p1.status_code == 200 and p2.status_code == 200
        ids1 = _person_ids(p1.json())
        ids2 = _person_ids(p2.json())
        assert len(ids1) == 2
        assert set(ids1).isdisjoint(set(ids2))
        # Re-fetch page 1 — stable
        p1b = _search(
            client,
            {
                "required": {"grade": {"values": ["EXPERT"]}},
                "sort": "NAME_ASC",
                "page": 1,
                "page_size": 2,
            },
        )
        assert _person_ids(p1b.json()) == ids1
    finally:
        for seeded in people:
            _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_invalid_code_returns_400(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ic_{suffix}", password="Secret123!")
    try:
        _login(client, user.login_id)
        resp = _search(client, {"required": {"jobs": ["JOB-DOES-NOT-EXIST"]}})
        assert resp.status_code == 400
        assert resp.json().get("code") == "SEARCH_INVALID_CODE"
    finally:
        _cleanup_user(db_session, user.id)


def test_rrf_helpers_unit() -> None:
    from app.modules.search.ranking import (
        compute_hybrid_relevance,
        relevance_to_score,
        rrf_score,
    )

    assert rrf_score(1) > rrf_score(2)
    a = compute_hybrid_relevance(
        keyword_rank=1,
        semantic_rank=2,
        preferred_match_ratio=0.5,
        preferred_present=True,
    )
    b = compute_hybrid_relevance(
        keyword_rank=1,
        semantic_rank=2,
        preferred_match_ratio=0.5,
        preferred_present=True,
    )
    assert a == b
    assert 0 <= relevance_to_score(a) <= 100


def test_matches_scaffold_required_preferred(client: TestClient, db_session) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"ms_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    codes = seeded["codes"]
    pref_missing = f"EXP-MISS-{suffix}"
    _ensure_code(db_session, pref_missing, "EXP", "MissingExp")
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {
                "required": {"grade": {"values": ["EXPERT"]}, "skills": [codes["tech"]]},
                "preferred": {"expertise": [codes["exp"], pref_missing]},
            },
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        matches = row["matches"]
        assert all(m["evidence_count"] == 0 for m in matches)
        required = [m for m in matches if m["type"] == "REQUIRED"]
        preferred = [m for m in matches if m["type"] == "PREFERRED"]
        assert required
        assert all(m["status"] == "MATCH" for m in required)
        assert any(m["status"] == "MATCH" for m in preferred)
        assert any(m["status"] == "NO_MATCH" for m in preferred)
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


# ---------------------------------------------------------------------------
# Correctness fixes: person-level channel best + required OR matches
# ---------------------------------------------------------------------------


def _eligible_subq(db, request=None):
    from app.modules.search.query_repository import SearchQueryRepository
    from app.modules.search.query_schemas import SearchPeopleRequest

    repo = SearchQueryRepository(db)
    req = request or SearchPeopleRequest()
    expanded = repo.validate_and_expand_codes(req)
    return repo.eligible_person_ids_subquery(
        required=req.required,
        expanded=expanded,
        skill_match_mode=req.skill_match_mode,
    ), repo


def test_keyword_many_items_one_person_does_not_crowd_out(db_session) -> None:
    """Person A with 6 strong keyword items must not crowd out Person B at limit=2."""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    a = _seed_person(db_session, suffix=f"ka{suffix}")
    b = _seed_person(db_session, suffix=f"kb{suffix}")
    try:
        # A: 6 strong exact matches (would fill old item-level LIMIT=6)
        for i in range(6):
            _add_index_item(
                db_session,
                person_id=a["person"].id,
                object_type="DOCUMENT_CHUNK",
                object_id=uuid.uuid4(),
                search_text=f"UNIQUEKW{suffix} chunk-{i} detailed content",
                source_weight="0.700",
            )
        # B: one slightly weaker but valid keyword hit
        _add_index_item(
            db_session,
            person_id=b["person"].id,
            object_type="PROJECT",
            object_id=b["project_a"].id,
            search_text=f"notes about UNIQUEKW{suffix} briefly",
            source_weight="1.000",
        )

        eligible_subq, repo = _eligible_subq(db_session)
        hits, truncated = repo.keyword_channel_hits(
            keyword=f"UNIQUEKW{suffix}",
            eligible_subq=eligible_subq,
            limit=2,
        )
        person_ids = {h.person_id for h in hits}
        assert a["person"].id in person_ids
        assert b["person"].id in person_ids
        assert len(hits) == 2
        # One best item per person
        assert len({h.person_id for h in hits}) == 2
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)


def test_semantic_many_items_one_person_does_not_crowd_out(db_session, monkeypatch) -> None:
    """Person A with many near DOCUMENT_CHUNK vectors must not crowd out Person B."""
    import uuid

    _enable_embedding(monkeypatch)
    suffix = uuid.uuid4().hex[:8]
    a = _seed_person(db_session, suffix=f"sa{suffix}")
    b = _seed_person(db_session, suffix=f"sb{suffix}")
    try:
        # Query-like vector on dim 0
        near = _unit_vector(index=0, value=1.0)
        mid = _unit_vector(index=0, value=0.8)
        # A: many near DOCUMENT_CHUNK items
        for i in range(6):
            _add_index_item(
                db_session,
                person_id=a["person"].id,
                object_type="DOCUMENT_CHUNK",
                object_id=uuid.uuid4(),
                search_text=f"chunk-{i}",
                source_weight="0.700",
                embedding=near,
            )
        # B: one PROJECT with next-best vector
        _add_index_item(
            db_session,
            person_id=b["person"].id,
            object_type="PROJECT",
            object_id=b["project_a"].id,
            search_text="project mid",
            source_weight="1.000",
            embedding=mid,
        )

        eligible_subq, repo = _eligible_subq(db_session)
        hits, truncated = repo.semantic_channel_hits(
            query_vector=near,
            eligible_subq=eligible_subq,
            limit=2,
        )
        person_ids = {h.person_id for h in hits}
        assert a["person"].id in person_ids
        assert b["person"].id in person_ids
        assert len(hits) == 2
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)


def test_semantic_source_weight_chooses_project_over_perfect_chunk(
    db_session, monkeypatch
) -> None:
    """Within one person: PROJECT .90*1.0 beats DOCUMENT_CHUNK 1.0*0.7."""
    import uuid

    _enable_embedding(monkeypatch)
    suffix = uuid.uuid4().hex[:8]
    seeded = _seed_person(db_session, suffix=suffix)
    try:
        query = _unit_vector(index=1, value=1.0)
        # Perfect chunk similarity (=1.0) but weight 0.7 → effective 0.7
        _add_index_item(
            db_session,
            person_id=seeded["person"].id,
            object_type="DOCUMENT_CHUNK",
            object_id=uuid.uuid4(),
            search_text="chunk perfect",
            source_weight="0.700",
            embedding=query,
        )
        # Near-project similarity 0.9 * 1.0 = 0.9 wins
        project_vec = _unit_vector(index=1, value=0.9)
        # normalize-ish: cosine with unit query on same axis ≈ 0.9 if we use single-axis vectors
        _add_index_item(
            db_session,
            person_id=seeded["person"].id,
            object_type="PROJECT",
            object_id=seeded["project_a"].id,
            search_text="project near",
            source_weight="1.000",
            embedding=project_vec,
        )

        eligible_subq, repo = _eligible_subq(db_session)
        hits, _ = repo.semantic_channel_hits(
            query_vector=query,
            eligible_subq=eligible_subq,
            limit=5,
        )
        assert len(hits) == 1
        assert hits[0].person_id == seeded["person"].id
        assert hits[0].object_type == "PROJECT"
        assert hits[0].raw_score > 0.7  # effective score of winning PROJECT
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def _mix_vector(*, primary: int, secondary: int, primary_w: float, secondary_w: float) -> list[float]:
    """Build a unit-ish 2-axis mix so cosine distances differ by angle, not magnitude."""
    import math

    vec = [0.0] * 1024
    vec[primary % 1024] = float(primary_w)
    vec[secondary % 1024] = float(secondary_w)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def test_document_rich_person_ranking_uses_best_hit_not_count(
    db_session, monkeypatch
) -> None:
    """Chunk count must not accumulate score or crowd candidate slots."""
    import uuid

    _enable_embedding(monkeypatch)
    suffix = uuid.uuid4().hex[:8]
    a = _seed_person(db_session, suffix=f"da{suffix}")
    b = _seed_person(db_session, suffix=f"db{suffix}")
    c = _seed_person(db_session, suffix=f"dc{suffix}")
    try:
        query = _unit_vector(index=2, value=1.0)
        # A: many weak/far DOCUMENT_CHUNK hits (orthogonal-ish)
        for i in range(10):
            _add_index_item(
                db_session,
                person_id=a["person"].id,
                object_type="DOCUMENT_CHUNK",
                object_id=uuid.uuid4(),
                search_text=f"doc-{i}",
                source_weight="0.700",
                embedding=_mix_vector(primary=5, secondary=6, primary_w=1.0, secondary_w=0.1),
            )
        # B: strong PROJECT near query
        _add_index_item(
            db_session,
            person_id=b["person"].id,
            object_type="PROJECT",
            object_id=b["project_a"].id,
            search_text="strong project",
            source_weight="1.000",
            embedding=_mix_vector(primary=2, secondary=3, primary_w=1.0, secondary_w=0.05),
        )
        # C: medium PROFILE
        _add_index_item(
            db_session,
            person_id=c["person"].id,
            object_type="PROFILE",
            object_id=c["person"].id,
            search_text="profile",
            source_weight="1.000",
            embedding=_mix_vector(primary=2, secondary=4, primary_w=0.6, secondary_w=0.8),
        )

        eligible_subq, repo = _eligible_subq(db_session)
        hits, _ = repo.semantic_channel_hits(
            query_vector=query,
            eligible_subq=eligible_subq,
            limit=3,
        )
        ordered = [h.person_id for h in hits]
        assert ordered[0] == b["person"].id
        assert set(ordered) == {a["person"].id, b["person"].id, c["person"].id}
        # Still one hit per person despite A's 10 chunks
        assert len(hits) == 3
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_person(db_session, c["person"].id)


def test_required_grade_or_matches_not_false_individual(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"mg_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    # seed person is EXPERT
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"grade": {"values": ["ADVANCED", "EXPERT"]}}},
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        required = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        conditions = [m["condition"] for m in required]
        # Must not claim ADVANCED alone as MATCH
        assert "ADVANCED" not in conditions
        assert "고급" not in conditions or any("OR" in c for c in conditions)
        assert any("OR" in c for c in conditions)
        assert all(m["status"] == "MATCH" for m in required)
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_required_skill_any_matches_aggregated(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"msa_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    codes = seeded["codes"]
    other = f"TECH-OTHER-{suffix}"
    _ensure_code(db_session, other, "TECH", "OtherTech")
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {
                "required": {"skills": [codes["tech"], other]},
                "skill_match_mode": "ANY",
            },
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        required = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        # OtherTech must not appear as a standalone MATCH
        assert all("OtherTech" not in m["condition"] or " OR " in m["condition"] for m in required)
        assert any(" OR " in m["condition"] for m in required)
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_required_skill_all_matches_per_skill(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"msl_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    codes = seeded["codes"]
    # seed person already has tech and tech2 via person/project in base seed?
    # Ensure both skills exist on person via seed codes tech + tech2
    t1, t2 = codes["tech"], codes["tech2"]
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"skills": [t1, t2]}, "skill_match_mode": "ALL"},
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        required = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        # ALL → individual MATCH items, no OR aggregation
        assert not any(" OR " in m["condition"] for m in required)
        assert len(required) >= 2
        assert all(m["status"] == "MATCH" for m in required)
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)


def test_required_jobs_or_matches_aggregated(
    client: TestClient, db_session
) -> None:
    suffix = uuid.uuid4().hex[:8]
    user = _create_user(db_session, login_id=f"mj_{suffix}", password="Secret123!")
    seeded = _seed_person(db_session, suffix=suffix)
    codes = seeded["codes"]
    other = f"JOB-OTHER-{suffix}"
    _ensure_code(db_session, other, "JOB", "OtherJob")
    try:
        _login(client, user.login_id)
        resp = _search(
            client,
            {"required": {"jobs": [codes["job"], other]}},
        )
        assert resp.status_code == 200, resp.text
        row = next(
            r for r in resp.json()["data"] if r["person_id"] == str(seeded["person"].id)
        )
        required = [m for m in row["matches"] if m["type"] == "REQUIRED"]
        assert any(" OR " in m["condition"] for m in required)
        assert all(
            "OtherJob" not in m["condition"] or " OR " in m["condition"] for m in required
        )
    finally:
        _cleanup_person(db_session, seeded["person"].id)
        _cleanup_user(db_session, user.id)



def test_semantic_ann_respects_required_hard_filter(db_session, monkeypatch) -> None:
    """Nearest vector person failing required grade must be excluded."""
    import uuid

    from app.db.models.person import PersonProfile

    _enable_embedding(monkeypatch)
    monkeypatch.setattr(
        "app.modules.search.ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD",
        0,
    )
    suffix = uuid.uuid4().hex[:8]
    near = _unit_vector(index=0, value=1.0)
    mid = _unit_vector(index=0, value=0.85)

    a = _seed_person(db_session, suffix=f"anna{suffix}")
    b = _seed_person(db_session, suffix=f"annb{suffix}")
    # Force hard-filter differentiation via technical_grade
    db_session.get(PersonProfile, a["person"].id).technical_grade = "BEGINNER"
    db_session.get(PersonProfile, b["person"].id).technical_grade = "EXPERT"
    db_session.commit()
    try:
        _add_index_item(
            db_session,
            person_id=a["person"].id,
            object_type="PROFILE",
            object_id=a["person"].id,
            search_text="near query",
            source_weight="1.000",
            embedding=near,
        )
        _add_index_item(
            db_session,
            person_id=b["person"].id,
            object_type="PROFILE",
            object_id=b["person"].id,
            search_text="mid query",
            source_weight="1.000",
            embedding=mid,
        )

        from app.modules.search.query_schemas import (
            GradeFilter,
            SearchConditionBlock,
            SearchPeopleRequest,
        )

        req = SearchPeopleRequest(
            required=SearchConditionBlock(grade=GradeFilter(values=["EXPERT"])),
            page=1,
            page_size=20,
        )
        eligible_subq, repo = _eligible_subq(db_session, request=req)
        hits, _ = repo.semantic_channel_hits(
            query_vector=near,
            eligible_subq=eligible_subq,
            limit=10,
        )
        ids = {h.person_id for h in hits}
        assert a["person"].id not in ids
        assert b["person"].id in ids
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)


def test_semantic_ignores_stale_embedding_version(db_session, monkeypatch) -> None:
    """Stale embedding_model/version rows must not beat current version."""
    import uuid

    _enable_embedding(monkeypatch)
    suffix = uuid.uuid4().hex[:8]
    near = _unit_vector(index=0, value=1.0)
    mid = _unit_vector(index=0, value=0.8)
    seeded = _seed_person(db_session, suffix=f"ver{suffix}")
    try:
        _add_index_item(
            db_session,
            person_id=seeded["person"].id,
            object_type="PROFILE",
            object_id=uuid.uuid4(),
            search_text="stale",
            source_weight="1.000",
            embedding=near,
            embedding_model="old-model",
            embedding_version="old-v0",
        )
        current_item = _add_index_item(
            db_session,
            person_id=seeded["person"].id,
            object_type="PROFILE",
            object_id=seeded["person"].id,
            search_text="current",
            source_weight="1.000",
            embedding=mid,
        )
        eligible_subq, repo = _eligible_subq(db_session)
        hits, _ = repo.semantic_channel_hits(
            query_vector=near,
            eligible_subq=eligible_subq,
            limit=10,
        )
        assert hits
        assert hits[0].person_id == seeded["person"].id
        assert hits[0].item_id == current_item.id
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_semantic_chunk_crowd_out_does_not_drop_profile_or_project(
    db_session, monkeypatch
) -> None:
    """Many near DOCUMENT_CHUNK rows for one person must not crowd out others."""
    import uuid

    _enable_embedding(monkeypatch)
    monkeypatch.setattr(
        "app.modules.search.ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD",
        0,
    )
    suffix = uuid.uuid4().hex[:8]
    near = _unit_vector(index=0, value=1.0)
    profile_vec = _unit_vector(index=0, value=0.92)
    project_vec = _unit_vector(index=0, value=0.90)
    a = _seed_person(db_session, suffix=f"cda{suffix}")
    b = _seed_person(db_session, suffix=f"cdb{suffix}")
    c = _seed_person(db_session, suffix=f"cdc{suffix}")
    try:
        for i in range(120):
            _add_index_item(
                db_session,
                person_id=a["person"].id,
                object_type="DOCUMENT_CHUNK",
                object_id=uuid.uuid4(),
                search_text=f"chunk-{i}",
                source_weight="0.700",
                embedding=near,
            )
        _add_index_item(
            db_session,
            person_id=b["person"].id,
            object_type="PROFILE",
            object_id=b["person"].id,
            search_text="profile near",
            source_weight="1.000",
            embedding=profile_vec,
        )
        _add_index_item(
            db_session,
            person_id=c["person"].id,
            object_type="PROJECT",
            object_id=c["project_a"].id,
            search_text="project near",
            source_weight="1.000",
            embedding=project_vec,
        )
        eligible_subq, repo = _eligible_subq(db_session)
        hits, _ = repo.semantic_channel_hits(
            query_vector=near,
            eligible_subq=eligible_subq,
            limit=2,
        )
        ids = {h.person_id for h in hits}
        # DOCUMENT_CHUNK source_weight 0.7 loses to PROFILE/PROJECT person-best.
        # Crowd-out regression: B and C must occupy the limit=2 slots (A cannot monopolize).
        assert b["person"].id in ids
        assert c["person"].id in ids
        assert a["person"].id not in ids
    finally:
        _cleanup_person(db_session, a["person"].id)
        _cleanup_person(db_session, b["person"].id)
        _cleanup_person(db_session, c["person"].id)
