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
        assert first["enqueued"] >= 3
        assert first["created_or_requeued"] == first["enqueued"]
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
        assert all(
            j.object_type in {"PROFILE", "PROJECT", "DOCUMENT_CHUNK"} for j in pending
        )
        item_ids = {j.payload_json.get("search_index_item_id") for j in pending}
        assert str(chunk.id) in item_ids
        assert str(inactive.id) not in item_ids
        assert second["enqueued"] == 0
        assert second["already_pending"] >= 2
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
        first, first_outcome = svc.repo.ensure_embedding_job(item)
        assert first is not None and first.status == "PENDING"
        assert first_outcome == "created"
        first.status = "FAILED"
        first.retry_count = 1
        first.completed_at = datetime.now(UTC)
        first.error_message = "embedding provider timeout"
        db_session.add(first)
        db_session.commit()

        again, again_outcome = svc.repo.ensure_embedding_job(item)
        assert again is not None
        assert again.status == "FAILED"
        assert again_outcome == "backoff"

        first.completed_at = datetime.now(UTC) - timedelta(
            seconds=settings.embedding_retry_backoff_seconds + 5
        )
        db_session.add(first)
        db_session.commit()
        requeued, requeued_outcome = svc.repo.ensure_embedding_job(item)
        assert requeued is not None and requeued.status == "PENDING"
        assert requeued.retry_count == 1
        assert requeued_outcome == "requeued"

        requeued.status = "FAILED"
        requeued.retry_count = settings.embedding_max_retries
        requeued.completed_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.add(requeued)
        db_session.commit()
        exhausted, exhausted_outcome = svc.repo.ensure_embedding_job(item)
        assert exhausted is not None and exhausted.status == "FAILED"
        assert exhausted.retry_count == settings.embedding_max_retries
        assert exhausted_outcome == "exhausted"
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


def test_queued_old_model_job_is_stale_noop(db_session, monkeypatch):
    """Queued job for old model must not call current provider or write vectors."""
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.embedding_policy import (
        current_embedding_model,
        effective_embedding_version,
    )
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch, model="old-model")
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider(_vector(fill=0.11))
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-old-model")
        SearchIndexService(db_session).process_job(job.id)
        old_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert old_jobs
        assert all(j.payload_json["embedding_model"] == "old-model" for j in old_jobs)

        _enable_embedding(monkeypatch, model="new-model")
        assert current_embedding_model() == "new-model"

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            for ej in old_jobs:
                result = SearchIndexService(db_session).process_job(ej.id)
                assert result.status == "COMPLETED"
        assert fake.calls == []

        for ej in old_jobs:
            db_session.refresh(ej)
            assert ej.status == "COMPLETED"
            item = db_session.get(
                SearchIndexItem, uuid.UUID(ej.payload_json["search_index_item_id"])
            )
            assert item is not None
            assert item.embedding is None

        scanned = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert scanned["enqueued"] >= 1
        new_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert new_jobs
        assert all(j.payload_json["embedding_model"] == "new-model" for j in new_jobs)

        fake2 = _FakeProvider(_vector(fill=0.22))
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake2,
        ):
            result = SearchIndexService(db_session).process_job(new_jobs[0].id)
        assert result.status == "COMPLETED"
        assert fake2.calls
        item = db_session.get(
            SearchIndexItem,
            uuid.UUID(new_jobs[0].payload_json["search_index_item_id"]),
        )
        assert item is not None
        assert item.embedding is not None
        assert item.embedding_model == "new-model"
        assert item.embedding_version == effective_embedding_version()
    finally:
        _cleanup_person(db_session, person.id)


def test_input_cap_version_change_old_job_stale(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.embedding_policy import effective_embedding_version
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch, max_chars=8000)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider()
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-cap")
        SearchIndexService(db_session).process_job(job.id)
        old_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == person.id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
            )
        ).first()
        assert old_job is not None
        assert old_job.payload_json["embedding_version"] == "embed-v1:c8000"
        old_pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )

        _enable_embedding(monkeypatch, max_chars=4000)
        assert effective_embedding_version() == "embed-v1:c4000"

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            for ej in old_pending:
                result = SearchIndexService(db_session).process_job(ej.id)
                assert result.status == "COMPLETED"
        assert fake.calls == []
        for ej in old_pending:
            db_session.refresh(ej)
            assert ej.status == "COMPLETED"

        scanned = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert scanned["enqueued"] >= 1
        new_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert new_jobs
        assert all(
            j.payload_json["embedding_version"] == "embed-v1:c4000" for j in new_jobs
        )
        new_job = new_jobs[0]

        long_text = "x" * 5000
        item = db_session.get(
            SearchIndexItem, uuid.UUID(new_job.payload_json["search_index_item_id"])
        )
        assert item is not None
        item.search_text = long_text
        meta = dict(item.metadata_json or {})
        from app.modules.search.document_builder import content_hash

        meta["content_hash"] = content_hash(long_text)
        item.metadata_json = meta
        new_job.payload_json = {
            **new_job.payload_json,
            "expected_content_hash": meta["content_hash"],
        }
        db_session.add_all([item, new_job])
        db_session.commit()

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(new_job.id)
        assert result.status == "COMPLETED"
        assert fake.calls
        assert len(fake.calls[0]) <= 4000
        db_session.refresh(item)
        assert item.embedding_version == "embed-v1:c4000"
    finally:
        _cleanup_person(db_session, person.id)


def test_search_document_version_change_creates_distinct_job(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.document_builder import content_hash
    from app.modules.search.embedding_policy import (
        current_embedding_model,
        effective_embedding_version,
        embedding_idempotency_key,
        item_content_hash,
    )
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider(_vector(fill=0.33))
    try:
        text = "same text"
        item = SearchIndexItem(
            id=uuid.uuid4(),
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
        job_v1, outcome = svc.repo.ensure_embedding_job(item)
        assert outcome == "created"
        assert job_v1 is not None
        assert job_v1.payload_json["expected_search_document_version"] == "search-doc-v1"

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = svc.process_job(job_v1.id)
        assert result.status == "COMPLETED"
        db_session.refresh(item)
        assert item.embedding is not None
        assert fake.calls

        # Rebuild semantics: same text, new search_document_version clears embedding
        item.embedding = None
        item.embedding_model = None
        item.embedding_version = None
        item.metadata_json = {
            "content_hash": content_hash(text),
            "search_document_version": "search-doc-v2",
        }
        db_session.add(item)
        db_session.commit()

        key_v1 = embedding_idempotency_key(
            search_index_item_id=str(item.id),
            content_hash_value=item_content_hash(item),
            search_document_version="search-doc-v1",
            embedding_model=current_embedding_model(),
            embedding_version=effective_embedding_version(),
        )
        key_v2 = embedding_idempotency_key(
            search_index_item_id=str(item.id),
            content_hash_value=item_content_hash(item),
            search_document_version="search-doc-v2",
            embedding_model=current_embedding_model(),
            embedding_version=effective_embedding_version(),
        )
        assert key_v1 != key_v2

        job_v2, outcome2 = svc.repo.ensure_embedding_job(item)
        assert outcome2 == "created"
        assert job_v2 is not None
        assert job_v2.id != job_v1.id
        assert job_v2.payload_json["expected_search_document_version"] == "search-doc-v2"
        assert job_v1.status == "COMPLETED"

        fake2 = _FakeProvider(_vector(fill=0.44))
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake2,
        ):
            result = svc.process_job(job_v2.id)
        assert result.status == "COMPLETED"
        db_session.refresh(item)
        assert item.embedding is not None
        assert item.embedding[0] == pytest.approx(0.44)
    finally:
        _cleanup_person(db_session, person.id)


def test_postcall_search_document_version_change_discards_vector(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-sdv-race")
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
                return _vector(fill=0.77)

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

        item = db_session.get(SearchIndexItem, item_id)
        assert item is not None
        meta = dict(item.metadata_json or {})
        meta["search_document_version"] = "search-doc-v2-changed"
        item.metadata_json = meta
        # Keep search_text / content_hash identical — only document version changes.
        db_session.add(item)
        db_session.commit()
        release.set()
        t.join(timeout=10)

        db_session.expire_all()
        finished = db_session.get(SearchIndexJob, embed_job.id)
        assert finished is not None
        assert finished.status == "COMPLETED"
        item2 = db_session.get(SearchIndexItem, item_id)
        assert item2 is not None
        assert item2.embedding is None
    finally:
        _cleanup_person(db_session, person.id)


def test_disabled_worker_cancels_and_reenable_recovers(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _enqueue_job, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider(_vector(fill=0.55))
    try:
        job = _enqueue_job(db_session, person.id, 3, key_suffix="emb-toggle")
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
        fingerprint = embed_job.idempotency_key
        retry_before = int(embed_job.retry_count or 0)

        _disable_embedding(monkeypatch)
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(embed_job.id)
        assert result.status == "CANCELLED"
        assert fake.calls == []
        db_session.refresh(embed_job)
        assert embed_job.status == "CANCELLED"
        assert embed_job.error_message is None
        assert int(embed_job.retry_count or 0) == retry_before
        item = db_session.get(SearchIndexItem, item_id)
        assert item is not None
        assert item.embedding is None

        _enable_embedding(monkeypatch)
        scanned = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert scanned["enqueued"] >= 1
        db_session.refresh(embed_job)
        assert embed_job.status == "PENDING"
        assert embed_job.idempotency_key == fingerprint

        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(embed_job.id)
        assert result.status == "COMPLETED"
        assert fake.calls
        db_session.refresh(item)
        assert item.embedding is not None
        db_session.refresh(embed_job)
        assert embed_job.status == "COMPLETED"
    finally:
        _cleanup_person(db_session, person.id)


def test_missing_model_version_payload_fails(db_session, monkeypatch):
    from app.db.models.search import SearchIndexJob
    from app.modules.search.embedding_policy import OPERATION_EMBED_SEARCH_INDEX_ITEM
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    fake = _FakeProvider()
    try:
        job = SearchIndexJob(
            person_id=person.id,
            object_type="PROFILE",
            object_id=person.id,
            action="UPSERT",
            status="PENDING",
            idempotency_key=f"people:{person.id}:missing-model",
            payload_json={
                "operation": OPERATION_EMBED_SEARCH_INDEX_ITEM,
                "search_index_item_id": str(uuid.uuid4()),
                "expected_content_hash": "a" * 64,
                "expected_search_document_version": "search-doc-v1",
                # embedding_model / embedding_version intentionally omitted
            },
        )
        db_session.add(job)
        db_session.commit()
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=fake,
        ):
            result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "FAILED"
        assert fake.calls == []
        db_session.refresh(job)
        assert job.status == "FAILED"
        assert "embedding_model" in (job.error_message or "")
    finally:
        _cleanup_person(db_session, person.id)


def test_scanner_enqueued_counts_only_created_or_requeued(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_builder import content_hash
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        text = "count me"
        item = SearchIndexItem(
            id=uuid.uuid4(),
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

        first = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert first["enqueued"] >= 1
        assert first["created_or_requeued"] == first["enqueued"]
        second = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert second["enqueued"] == 0
        assert second["already_pending"] >= 1
    finally:
        _cleanup_person(db_session, person.id)


def test_concurrent_ensure_embedding_job_unique_idempotency(db_session, monkeypatch):
    """Two sessions racing ensure_embedding_job must yield exactly one job row."""
    import threading

    from sqlalchemy import func, select

    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.document_builder import content_hash
    from app.modules.search.embedding_policy import embedding_idempotency_key
    from app.modules.search.repository import SearchRepository
    from app.modules.search.service import SearchIndexService
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        text = "concurrent embed target"
        item = SearchIndexItem(
            id=uuid.uuid4(),
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
        item_id = item.id

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        job_ids: list[uuid.UUID] = []
        errors: list[BaseException] = []
        commits_ok: list[bool] = []

        def worker() -> None:
            db = SessionLocal()
            try:
                local_item = db.get(SearchIndexItem, item_id)
                assert local_item is not None
                barrier.wait(timeout=5)
                job, outcome = SearchRepository(db).ensure_embedding_job(local_item)
                assert job is not None
                db.commit()
                outcomes.append(outcome)
                job_ids.append(job.id)
                commits_ok.append(True)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert errors == [], f"unexpected errors: {errors!r}"
        assert commits_ok == [True, True]
        assert len(outcomes) == 2
        assert "created" in outcomes
        assert outcomes.count("created") == 1
        other = [o for o in outcomes if o != "created"]
        assert other == ["already_pending"]

        db_session.expire_all()
        rows = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].status == "PENDING"
        assert rows[0].idempotency_key is not None
        assert job_ids[0] == job_ids[1] == rows[0].id

        # Fingerprint material still includes search_document_version.
        from app.modules.search.embedding_policy import (
            current_embedding_model,
            effective_embedding_version,
            item_content_hash,
            item_search_document_version,
        )

        expected_key = embedding_idempotency_key(
            search_index_item_id=str(item_id),
            content_hash_value=item_content_hash(rows[0] and db_session.get(SearchIndexItem, item_id)),
            search_document_version=item_search_document_version(
                db_session.get(SearchIndexItem, item_id)
            ),
            embedding_model=current_embedding_model(),
            embedding_version=effective_embedding_version(),
        )
        assert rows[0].idempotency_key == expected_key
        assert rows[0].payload_json["operation"] == "EMBED_SEARCH_INDEX_ITEM"
        assert rows[0].payload_json["expected_search_document_version"] == "search-doc-v1"
        assert rows[0].payload_json["embedding_model"] == current_embedding_model()
        assert rows[0].payload_json["embedding_version"] == effective_embedding_version()

        # Scanner must not create a second row for the same fingerprint.
        scanned = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert scanned["enqueued"] == 0
        count = db_session.scalar(
            select(func.count()).select_from(SearchIndexJob).where(
                SearchIndexJob.idempotency_key == expected_key
            )
        )
        assert count == 1
    finally:
        _cleanup_person(db_session, person.id)


def test_ensure_embedding_conflict_does_not_rollback_caller_tx(db_session, monkeypatch):
    """UNIQUE race must not abort an open caller transaction (REBUILD-like)."""
    import threading

    from sqlalchemy import select

    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.document_builder import content_hash
    from app.modules.search.repository import SearchRepository
    from tests.test_search_index import _cleanup_person, _seed_person

    _enable_embedding(monkeypatch)
    seeded = _seed_person(db_session)
    person = seeded["person"]
    try:
        text = "rebuild race text"
        item = SearchIndexItem(
            id=uuid.uuid4(),
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
        item_id = item.id

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []
        outcomes: dict[str, str] = {}
        marker = "mutated-by-rebuild-tx-UNIQUE-XYZ"

        def rebuild_like_worker() -> None:
            db = SessionLocal()
            try:
                local = db.get(SearchIndexItem, item_id)
                assert local is not None
                # Simulate REBUILD_PERSON mutations already in the same TX.
                local.search_text = marker
                meta = dict(local.metadata_json or {})
                meta["content_hash"] = content_hash(marker)
                # Keep search_document_version identical so fingerprint race is on
                # the *pre-mutation* item loaded by the scanner thread; rebuild
                # ensure uses post-mutation fingerprint. To force same key, ensure
                # after barrier using the original snapshot fields via scanner,
                # while rebuild ensures the mutated item (different key).
                #
                # Instead: both ensure the same committed snapshot. Rebuild TX
                # only flips a non-fingerprint field (source_weight) so the
                # UNIQUE key still collides, proving TX survival.
                local.search_text = text
                meta["content_hash"] = content_hash(text)
                local.metadata_json = meta
                local.source_weight = Decimal("0.500")
                db.flush()
                barrier.wait(timeout=5)
                job, outcome = SearchRepository(db).ensure_embedding_job(local)
                assert job is not None
                db.commit()
                outcomes["rebuild"] = outcome
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

        def scanner_worker() -> None:
            db = SessionLocal()
            try:
                local = db.get(SearchIndexItem, item_id)
                assert local is not None
                barrier.wait(timeout=5)
                job, outcome = SearchRepository(db).ensure_embedding_job(local)
                assert job is not None
                db.commit()
                outcomes["scanner"] = outcome
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

        t_rebuild = threading.Thread(target=rebuild_like_worker)
        t_scanner = threading.Thread(target=scanner_worker)
        t_rebuild.start()
        t_scanner.start()
        t_rebuild.join(timeout=15)
        t_scanner.join(timeout=15)

        assert errors == [], f"unexpected errors: {errors!r}"
        assert set(outcomes) == {"rebuild", "scanner"}
        assert "created" in outcomes.values()
        assert outcomes["rebuild"] in {"created", "already_pending"}
        assert outcomes["scanner"] in {"created", "already_pending"}
        assert outcomes["rebuild"] != outcomes["scanner"] or outcomes["rebuild"] == "already_pending"

        db_session.expire_all()
        item2 = db_session.get(SearchIndexItem, item_id)
        assert item2 is not None
        # Rebuild-like TX mutations survived the UNIQUE race.
        assert item2.source_weight == Decimal("0.500")
        jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person.id,
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == "PENDING"
    finally:
        _cleanup_person(db_session, person.id)
