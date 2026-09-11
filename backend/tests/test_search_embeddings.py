"""BGE-M3 Embedding Provider + PROFILE/PROJECT embedding worker tests."""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ.setdefault("APP_ENV", "test")



@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()

@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _enable_embedding(monkeypatch, **overrides):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_BASE_URL", overrides.get("base_url", "http://embed.test"))
    monkeypatch.setenv("EMBEDDING_API_KEY", overrides.get("api_key", "test-key"))
    monkeypatch.setenv("EMBEDDING_MODEL", overrides.get("model", "bge-m3"))
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("EMBEDDING_MAX_INPUT_CHARS", str(overrides.get("max_chars", 8000)))
    monkeypatch.setenv("EMBEDDING_MAX_RETRIES", str(overrides.get("max_retries", 3)))
    monkeypatch.setenv(
        "EMBEDDING_RETRY_BACKOFF_SECONDS", str(overrides.get("backoff", 60))
    )
    get_settings.cache_clear()
    return get_settings()


def _disable_embedding(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("EMBEDDING_ENABLED", "false")
    get_settings.cache_clear()


def _vector(dim: int = 1024, fill: float = 0.01) -> list[float]:
    return [float(fill)] * dim


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


def _patch_http_client(transport: httpx.MockTransport):
    real_client = httpx.Client

    def _factory(*_a, **k):
        return real_client(transport=transport, timeout=k.get("timeout"))

    return patch("app.ai.providers.openai_compat.httpx.Client", side_effect=_factory)


def test_normalize_embeddings_url():
    from app.ai.providers.errors import AIProviderError
    from app.ai.providers.openai_compat import normalize_embeddings_url

    assert normalize_embeddings_url("https://host") == "https://host/v1/embeddings"
    assert normalize_embeddings_url("https://host/v1") == "https://host/v1/embeddings"
    assert (
        normalize_embeddings_url("https://host/v1/embeddings")
        == "https://host/v1/embeddings"
    )
    with pytest.raises(AIProviderError):
        normalize_embeddings_url("ftp://host")


def test_provider_basic_request_and_dimension(monkeypatch):
    from app.ai.providers.embedding import OpenAICompatibleEmbeddingProvider

    settings = _enable_embedding(monkeypatch)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode())
        captured["body"] = body
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": _vector()}],
                "model": settings.embedding_model,
            },
        )

    with _patch_http_client(httpx.MockTransport(handler)):
        vec = OpenAICompatibleEmbeddingProvider().embed_text("Python RAG 개발")
    assert len(vec) == 1024
    assert captured["body"]["model"] == settings.embedding_model
    assert captured["body"]["input"] == ["Python RAG 개발"]
    assert captured["auth"] == "Bearer test-key"


def test_provider_batch_reorders_by_index(monkeypatch):
    from app.ai.providers.embedding import OpenAICompatibleEmbeddingProvider

    _enable_embedding(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": _vector(fill=0.2)},
                    {"index": 0, "embedding": _vector(fill=0.1)},
                ],
                "model": "bge-m3",
            },
        )

    with _patch_http_client(httpx.MockTransport(handler)):
        out = OpenAICompatibleEmbeddingProvider().embed_texts(["A", "B"])
    assert out[0][0] == pytest.approx(0.1)
    assert out[1][0] == pytest.approx(0.2)


def test_provider_rejects_bad_dimension_and_nonfinite(monkeypatch):
    from app.ai.providers.embedding import OpenAICompatibleEmbeddingProvider
    from app.ai.providers.errors import AIResponseValidationError

    _enable_embedding(monkeypatch)

    def bad_dim(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": _vector(768)}], "model": "bge-m3"},
        )

    with _patch_http_client(httpx.MockTransport(bad_dim)):
        with pytest.raises(AIResponseValidationError, match="dimension"):
            OpenAICompatibleEmbeddingProvider().embed_text("x")

    # JSON cannot encode NaN; inject a parsed payload via post_embeddings mock.
    with patch(
        "app.ai.providers.embedding.post_embeddings",
        return_value={
            "data": [{"index": 0, "embedding": [math.nan] + [0.01] * 1023}],
            "model": "bge-m3",
        },
    ):
        with pytest.raises(AIResponseValidationError):
            OpenAICompatibleEmbeddingProvider().embed_text("x")


def test_input_cap_is_deterministic(monkeypatch):
    from app.modules.search.embedding_policy import prepare_embedding_input

    _enable_embedding(monkeypatch, max_chars=10)
    text = "abcdefghijklmnop"
    a = prepare_embedding_input(text)
    b = prepare_embedding_input(text)
    assert a == b == "abcdefghij"
    assert len(a) == 10


def test_rebuild_enqueues_embedding_jobs(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.modules.search.embedding_policy import OPERATION_EMBED_SEARCH_INDEX_ITEM
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-rebuild")
        SearchIndexService(db_session).process_job(job.id)
        embed_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert len(embed_jobs) == 3
        for ej in embed_jobs:
            assert ej.payload_json["operation"] == OPERATION_EMBED_SEARCH_INDEX_ITEM
            assert ej.object_type in {"PROFILE", "PROJECT"}
            assert ej.object_id is not None
            assert ej.person_id == person.id
    finally:
        _cleanup_person(db_session, person.id)


def test_embedding_worker_success(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.embedding_policy import (
        current_embedding_model,
        effective_embedding_version,
    )
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider(_vector(fill=0.42))
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-ok")
        SearchIndexService(db_session).process_job(job.id)
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).first()
        assert embed_job is not None
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(embed_job.id)
        assert result.status == "COMPLETED"
        assert result.claimed is True
        assert fake.calls
        item = db_session.get(
            SearchIndexItem, uuid.UUID(embed_job.payload_json["search_index_item_id"])
        )
        assert item is not None
        assert item.embedding is not None
        assert len(item.embedding) == 1024
        assert item.embedding_model == current_embedding_model()
        assert item.embedding_version == effective_embedding_version()
        assert item.metadata_json.get("content_hash")
    finally:
        _cleanup_person(db_session, person.id)


def test_already_current_skips_enqueue(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.embedding_policy import (
        current_embedding_model,
        effective_embedding_version,
    )
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-current")
        SearchIndexService(db_session).process_job(job.id)
        items = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person.id,
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        for item in items:
            item.embedding = _vector()
            item.embedding_model = current_embedding_model()
            item.embedding_version = effective_embedding_version()
            db_session.add(item)
        for ej in db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).all():
            ej.status = "CANCELLED"
            db_session.add(ej)
        db_session.commit()

        job2 = _enqueue_job(db_session, person.id, 3, key_suffix="emb-current-2")
        SearchIndexService(db_session).process_job(job2.id)
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert pending == []
    finally:
        _cleanup_person(db_session, person.id)


def test_model_change_reenqueues(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch, model="bge-m3")
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-model")
        SearchIndexService(db_session).process_job(job.id)
        for ej in db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
            )
        ).all():
            ej.status = "CANCELLED"
            db_session.add(ej)
        for item in db_session.scalars(
            select(SearchIndexItem).where(SearchIndexItem.person_id == person.id)
        ).all():
            item.embedding = _vector()
            item.embedding_model = "old-model"
            item.embedding_version = "embed-v1:c8000"
            db_session.add(item)
        db_session.commit()

        scanned = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert scanned["enqueued"] >= 1
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert pending
        assert pending[0].payload_json["embedding_model"] == "bge-m3"
    finally:
        _cleanup_person(db_session, person.id)


def test_precall_stale_job_skips_provider(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider()
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-stale")
        SearchIndexService(db_session).process_job(job.id)
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).first()
        assert embed_job is not None
        embed_job.payload_json = {
            **embed_job.payload_json,
            "expected_content_hash": "0" * 64,
        }
        db_session.add(embed_job)
        db_session.commit()

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(embed_job.id)
        assert result.status == "COMPLETED"
        assert fake.calls == []
    finally:
        _cleanup_person(db_session, person.id)


def test_midcall_content_change_discards_vector(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    profile = seeded["profile"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-race")
        SearchIndexService(db_session).process_job(job.id)
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.object_type == "PROFILE",
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).one()
        item_id = uuid.UUID(embed_job.payload_json["search_index_item_id"])
        entered = threading.Event()
        release = threading.Event()

        class BlockingProvider:
            def embed_text(self, text: str) -> list[float]:
                entered.set()
                assert release.wait(timeout=5)
                return _vector(fill=0.7)

        def worker():
            db = SessionLocal()
            try:
                with patch(
                    "app.ai.providers.embedding.get_embedding_provider",
                    return_value=BlockingProvider(),
                ):
                    SearchIndexService(db).process_job(embed_job.id)
            finally:
                db.close()

        t = threading.Thread(target=worker)
        t.start()
        assert entered.wait(timeout=5)
        profile.profile_summary = "race-changed-summary-UNIQUE-XYZ"
        profile.profile_version = 4
        db_session.add(profile)
        db_session.commit()
        job2 = _enqueue_job(db_session, person.id, 4, key_suffix="emb-race-rebuild")
        SearchIndexService(db_session).process_job(job2.id)
        release.set()
        t.join(timeout=10)

        db_session.expire_all()
        finished = db_session.get(SearchIndexJob, embed_job.id)
        assert finished is not None
        assert finished.status == "COMPLETED"
        # Stale H1 vector must not be persisted on the live PROFILE row.
        active = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == person.id,
                SearchIndexItem.object_type == "PROFILE",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert "race-changed-summary-UNIQUE-XYZ" in (active.search_text or "")
        assert active.embedding is None or active.embedding[0] != pytest.approx(0.7)
        old = db_session.get(SearchIndexItem, item_id)
        if old is not None and old.id == active.id:
            assert old.embedding is None or old.embedding[0] != pytest.approx(0.7)
    finally:
        _cleanup_person(db_session, person.id)


def test_duplicate_worker_single_provider_call(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-dup")
        SearchIndexService(db_session).process_job(job.id)
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).first()
        assert embed_job is not None

        entered = threading.Event()
        release = threading.Event()
        calls: list[int] = []

        class BlockingProvider:
            def embed_text(self, text: str) -> list[float]:
                calls.append(1)
                entered.set()
                assert release.wait(timeout=5)
                return _vector()

        results = []

        def worker():
            db = SessionLocal()
            try:
                with patch(
                    "app.ai.providers.embedding.get_embedding_provider",
                    return_value=BlockingProvider(),
                ):
                    results.append(SearchIndexService(db).process_job(embed_job.id))
            finally:
                db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        assert entered.wait(timeout=5)
        t2.start()
        time.sleep(0.2)
        release.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert len(calls) == 1
        assert any(r.status == "COMPLETED" and r.claimed for r in results)
    finally:
        _cleanup_person(db_session, person.id)


def test_scanner_backfill_and_exclusions(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.document_builder import content_hash
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        profile_text = "profile text"
        project_text = "project text"
        profile_item = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            search_text=profile_text,
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={
                "content_hash": content_hash(profile_text),
                "search_document_version": "search-doc-v1",
            },
            is_active=True,
        )
        project_item = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="PROJECT",
            object_id=seeded["project_a"].id,
            search_text=project_text,
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={
                "content_hash": content_hash(project_text),
                "search_document_version": "search-doc-v1",
            },
            is_active=True,
        )
        chunk = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="DOCUMENT_CHUNK",
            object_id=uuid.uuid4(),
            search_text="chunk",
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={},
            is_active=True,
        )
        inactive = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=person.id,
            object_type="PROFILE",
            object_id=uuid.uuid4(),
            search_text="inactive",
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={"content_hash": content_hash("inactive")},
            is_active=False,
        )
        db_session.add_all([profile_item, project_item, chunk, inactive])
        db_session.commit()

        first = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        second = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert first["enqueued"] >= 2
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        keys = {j.idempotency_key for j in pending}
        assert len(keys) == len(pending)
        assert all(j.object_type in {"PROFILE", "PROJECT"} for j in pending)
        item_ids = {j.payload_json.get("search_index_item_id") for j in pending}
        assert str(chunk.id) not in item_ids
        assert str(inactive.id) not in item_ids
        assert second["enqueued"] >= 2
        pending2 = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert len({j.idempotency_key for j in pending2}) == len(pending2)
    finally:
        _cleanup_person(db_session, person.id)


def test_embedding_disabled_no_jobs(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _disable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-off")
        result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "COMPLETED"
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        assert pending == []
        assert SearchIndexService(db_session).enqueue_missing_embeddings()["enqueued"] == 0
    finally:
        _cleanup_person(db_session, person.id)


def test_provider_failure_keeps_item_and_sanitizes(db_session, monkeypatch):
    from app.ai.providers.errors import AIProviderError
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from app.tasks.index_tasks import process_search_index_job
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    secret = "홍길동 secret@example.com SEARCH-SECRET"
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-fail")
        SearchIndexService(db_session).process_job(job.id)
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).first()
        assert embed_job is not None
        item_id = uuid.UUID(embed_job.payload_json["search_index_item_id"])
        before = db_session.get(SearchIndexItem, item_id)
        before_text = before.search_text

        class Boom:
            def embed_text(self, text: str):
                raise AIProviderError(f"embedding provider timeout {secret}")

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=Boom(),
        ):
            with pytest.raises(Exception):
                process_search_index_job(str(embed_job.id))

        db_session.expire_all()
        row = db_session.get(SearchIndexJob, embed_job.id)
        item = db_session.get(SearchIndexItem, item_id)
        assert row is not None and row.status == "FAILED"
        assert row.retry_count == 1
        err = row.error_message or ""
        assert secret not in err
        assert "홍길동" not in err
        assert "secret@example.com" not in err
        assert "SEARCH-SECRET" not in err
        assert "timeout" in err
        assert item is not None
        assert item.search_text == before_text
        assert item.embedding is None
    finally:
        _cleanup_person(db_session, person.id)


def test_retry_backoff_and_exhaustion(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_builder import content_hash
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    settings = _enable_embedding(monkeypatch, max_retries=2, backoff=3600)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        item_id = uuid.uuid4()
        text = "need embed"
        item = SearchIndexItem(
            id=item_id,
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            search_text=text,
            embedding=None,
            source_weight=Decimal("1.000"),
            metadata_json={
                "content_hash": content_hash(text),
                "search_document_version": "search-doc-v1",
            },
            is_active=True,
        )
        db_session.add(item)
        db_session.commit()

        svc = SearchIndexService(db_session)
        first = svc.repo.ensure_embedding_job(item)
        assert first is not None and first.status == "PENDING"
        first.status = "FAILED"
        first.retry_count = 1
        first.completed_at = datetime.now(UTC)
        first.error_message = "embedding provider timeout"
        db_session.add(first)
        db_session.commit()

        again = svc.repo.ensure_embedding_job(item)
        assert again is not None
        assert again.status == "FAILED"

        first.completed_at = datetime.now(UTC) - timedelta(
            seconds=settings.embedding_retry_backoff_seconds + 5
        )
        db_session.add(first)
        db_session.commit()
        requeued = svc.repo.ensure_embedding_job(item)
        assert requeued is not None and requeued.status == "PENDING"
        assert requeued.retry_count == 1

        requeued.status = "FAILED"
        requeued.retry_count = settings.embedding_max_retries
        requeued.completed_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.add(requeued)
        db_session.commit()
        exhausted = svc.repo.ensure_embedding_job(item)
        assert exhausted is not None and exhausted.status == "FAILED"
        assert exhausted.retry_count == settings.embedding_max_retries
    finally:
        _cleanup_person(db_session, person.id)


def test_unsupported_upsert_operation_fails(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = SearchIndexJob(
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            action="UPSERT",
            status="PENDING",
            idempotency_key=f"people:{person.id}:bad-upsert",
            payload_json={"operation": "SOMETHING_ELSE"},
        )
        db_session.add(job)
        db_session.commit()
        result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "FAILED"
        db_session.refresh(job)
        assert job.status == "FAILED"
        assert "unsupported" in (job.error_message or "").lower()
    finally:
        _cleanup_person(db_session, person.id)
