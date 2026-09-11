"""DocumentChunk materialization + DOCUMENT_CHUNK SearchIndexItem/Embedding tests."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

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


def _vector(dim: int = 1024, fill: float = 0.01) -> list[float]:
    return [float(fill)] * dim


class _FakeProvider:
    def __init__(self, vector: list[float] | None = None):
        self.vector = vector if vector is not None else _vector()
        self.calls: list[str] = []

    def embed_text(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


def _ensure_doc_type(db) -> str:
    from app.db.models.code import CodeMaster

    code = "DOC-CAREER"
    if db.get(CodeMaster, code) is None:
        db.add(
            CodeMaster(
                code=code,
                code_type="DOC_TYPE",
                name="경력기술서",
                sort_order=0,
                is_active=True,
            )
        )
        db.commit()
    return code


def _create_user(db, *, login_id: str | None = None):
    from app.core.security import hash_password
    from app.db.models.user import AppUser

    user = AppUser(
        login_id=login_id or f"chunk_{uuid.uuid4().hex[:10]}",
        password_hash=hash_password("Secret123!"),
        name="Chunk Tester",
        role="ADMIN",
        status="ACTIVE",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _cleanup_person(db, person_id, user_id=None) -> None:
    from app.db.models.document import Document, DocumentChunk, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile
    from app.db.models.revision import AuditLog, ProfileRevision
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.models.user import AppUser

    group_ids = list(
        db.scalars(select(DocumentGroup.id).where(DocumentGroup.person_id == person_id)).all()
    )
    doc_ids: list = []
    for gid in group_ids:
        doc_ids.extend(
            list(
                db.scalars(
                    select(Document.id).where(Document.document_group_id == gid)
                ).all()
            )
        )
    for did in doc_ids:
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == did))
        db.execute(delete(DocumentPage).where(DocumentPage.document_id == did))
    for gid in group_ids:
        db.execute(delete(Document).where(Document.document_group_id == gid))
    db.execute(delete(DocumentGroup).where(DocumentGroup.person_id == person_id))
    db.execute(delete(SearchIndexItem).where(SearchIndexItem.person_id == person_id))
    db.execute(delete(SearchIndexJob).where(SearchIndexJob.person_id == person_id))
    db.execute(delete(ProfileRevision).where(ProfileRevision.person_id == person_id))
    db.execute(delete(AuditLog).where(AuditLog.target_id == person_id))
    if user_id is not None:
        db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
    db.execute(delete(PersonProfile).where(PersonProfile.person_id == person_id))
    db.execute(delete(Person).where(Person.id == person_id))
    if user_id is not None:
        db.execute(delete(AppUser).where(AppUser.id == user_id))
    db.commit()


def _seed_person_group(
    db,
    *,
    pages: list[tuple[int, str | None, str | None]] | None = None,
    processing_status: str = "READY",
    version_no: int = 1,
    person_status: str = "ACTIVE",
):
    """Create person + group + document + pages.

    pages: list of (page_no, extracted_text, extraction_method)
    """
    from app.db.models.document import Document, DocumentGroup, DocumentPage
    from app.db.models.person import Person, PersonProfile

    _ensure_doc_type(db)
    person = Person(status=person_status)
    db.add(person)
    db.flush()
    db.add(
        PersonProfile(
            person_id=person.id,
            name="청크테스터",
            profile_version=1,
        )
    )
    group = DocumentGroup(
        person_id=person.id,
        document_type_code="DOC-CAREER",
        title="경력기술서",
    )
    db.add(group)
    db.flush()
    document = Document(
        document_group_id=group.id,
        version_no=version_no,
        is_latest=True,
        original_filename="career.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256="b" * 64,
        processing_status=processing_status,
        updated_at=datetime.now(UTC),
    )
    db.add(document)
    db.flush()
    if pages is None:
        pages = [(1, "짧은 원문 텍스트입니다.", "TEXT_PARSER")]
    for page_no, text, method in pages:
        layout = {"needs_vlm": text is None}
        db.add(
            DocumentPage(
                document_id=document.id,
                page_no=page_no,
                extracted_text=text,
                layout_json=layout,
                extraction_method=method,
            )
        )
    db.commit()
    return {"person": person, "group": group, "document": document}


def _add_document_version(
    db,
    group,
    *,
    version_no: int,
    pages: list[tuple[int, str | None, str | None]],
    processing_status: str = "READY",
    is_latest: bool = True,
):
    from app.db.models.document import Document, DocumentPage

    if is_latest:
        for row in db.scalars(
            select(Document).where(Document.document_group_id == group.id)
        ).all():
            row.is_latest = False
            db.add(row)
    document = Document(
        document_group_id=group.id,
        version_no=version_no,
        is_latest=is_latest,
        original_filename=f"career_v{version_no}.pdf",
        extension="pdf",
        mime_type="application/pdf",
        file_size=100,
        storage_key=f"test/{uuid.uuid4()}.pdf",
        sha256=hashlib.sha256(str(version_no).encode()).hexdigest(),
        processing_status=processing_status,
        updated_at=datetime.now(UTC),
    )
    db.add(document)
    db.flush()
    for page_no, text, method in pages:
        db.add(
            DocumentPage(
                document_id=document.id,
                page_no=page_no,
                extracted_text=text,
                layout_json={"needs_vlm": text is None},
                extraction_method=method,
            )
        )
    db.commit()
    return document


def _sync_group(db, group_id, monkeypatch=None):
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    if monkeypatch is not None:
        _enable_embedding(monkeypatch)
    job, _outcome = DocumentChunkSyncService(db).ensure_sync_job(group_id)
    db.commit()
    assert job is not None
    result = SearchIndexService(db).process_job(job.id)
    assert result.status == "COMPLETED"
    return result


# ---------------------------------------------------------------------------
# Chunker unit tests
# ---------------------------------------------------------------------------


def test_chunker_blank_page_zero_chunks():
    from app.modules.document_processing.chunker import chunk_page_text

    assert chunk_page_text("   \n\t  ", page_no=1, extraction_method="T", source_fingerprint="x", start_index=0) == []
    assert chunk_page_text("", page_no=1, extraction_method="T", source_fingerprint="x", start_index=0) == []


def test_chunker_short_page_one_chunk_exact_substring():
    from app.modules.document_processing.chunker import chunk_hash, chunk_page_text

    text = "  hello world  "
    specs = chunk_page_text(
        text, page_no=3, extraction_method="TEXT_PARSER", source_fingerprint="fp", start_index=0
    )
    assert len(specs) == 1
    s = specs[0]
    assert s.page_from == s.page_to == 3
    assert text[s.char_start : s.char_end] == s.chunk_text == "hello world"
    assert s.chunk_hash == chunk_hash("hello world")
    assert s.chunk_hash == hashlib.sha256(b"hello world").hexdigest()
    assert len(s.chunk_hash) == 64


def test_chunker_long_page_deterministic_overlap_forward():
    from app.modules.document_processing.chunker import (
        DOCUMENT_CHUNK_MAX_CHARS,
        DOCUMENT_CHUNK_OVERLAP_CHARS,
        build_chunk_specs_for_pages,
        chunk_page_text,
    )

    # Force multiple chunks with paragraph boundaries.
    paragraph = ("alpha beta gamma delta epsilon zeta\n\n" * 40)
    text = paragraph + paragraph
    assert len(text) > DOCUMENT_CHUNK_MAX_CHARS
    specs = chunk_page_text(
        text,
        page_no=1,
        extraction_method="TEXT_PARSER",
        source_fingerprint="fp",
        start_index=0,
        max_chars=DOCUMENT_CHUNK_MAX_CHARS,
        overlap_chars=DOCUMENT_CHUNK_OVERLAP_CHARS,
    )
    assert len(specs) >= 2
    for s in specs:
        assert len(s.chunk_text) <= DOCUMENT_CHUNK_MAX_CHARS
        assert text[s.char_start : s.char_end] == s.chunk_text
        assert s.page_from == s.page_to == 1
    # Forward progress on char_start
    for i in range(1, len(specs)):
        assert specs[i].char_start > specs[i - 1].char_start
    # Overlap between consecutive windows (when overlap configured)
    if DOCUMENT_CHUNK_OVERLAP_CHARS > 0 and len(specs) >= 2:
        assert specs[1].char_start < specs[0].char_end

    pages = [SimpleNamespace(page_no=1, extracted_text=text, extraction_method="TEXT_PARSER")]
    a, fa = build_chunk_specs_for_pages(pages)
    b, fb = build_chunk_specs_for_pages(pages)
    assert fa == fb
    assert [(s.chunk_index, s.char_start, s.char_end, s.chunk_text, s.chunk_hash) for s in a] == [
        (s.chunk_index, s.char_start, s.char_end, s.chunk_text, s.chunk_hash) for s in b
    ]


def test_chunker_multi_page_skips_blank_page_local():
    from app.modules.document_processing.chunker import (
        DOCUMENT_CHUNK_MAX_CHARS,
        build_chunk_specs_for_pages,
    )

    long_text = ("line\n" * 500)
    assert len(long_text) > DOCUMENT_CHUNK_MAX_CHARS
    pages = [
        SimpleNamespace(page_no=1, extracted_text="short p1", extraction_method="TEXT_PARSER"),
        SimpleNamespace(page_no=2, extracted_text=long_text, extraction_method="TEXT_PARSER"),
        SimpleNamespace(page_no=3, extracted_text="   ", extraction_method="TEXT_PARSER"),
        SimpleNamespace(page_no=4, extracted_text="short p4", extraction_method="OCR"),
    ]
    specs, _fp = build_chunk_specs_for_pages(pages)
    assert specs[0].page_no == 1 and specs[0].chunk_index == 0
    assert all(s.page_from == s.page_to for s in specs)
    assert not any(s.page_no == 3 for s in specs)
    assert specs[-1].page_no == 4
    indexes = [s.chunk_index for s in specs]
    assert indexes == list(range(len(specs)))


def test_chunker_never_zero_length_or_stuck():
    from app.modules.document_processing.chunker import chunk_page_text

    text = "a" * 5000
    specs = chunk_page_text(
        text,
        page_no=1,
        extraction_method="T",
        source_fingerprint="fp",
        start_index=0,
        max_chars=100,
        overlap_chars=99,
    )
    assert specs
    assert all(len(s.chunk_text) > 0 for s in specs)
    starts = [s.char_start for s in specs]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


# ---------------------------------------------------------------------------
# Materialization / sync integration
# ---------------------------------------------------------------------------


def test_materialization_idempotent_preserves_ids(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_chunk_policy import (
        DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION,
        SOURCE_WEIGHT_DOCUMENT_CHUNK,
    )
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService

    seeded = _seed_person_group(
        db_session,
        pages=[
            (1, "페이지1 내용", "TEXT_PARSER"),
            (2, "페이지2 내용이 조금 더 깁니다.", "TEXT_PARSER"),
        ],
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        chunks1 = list(
            db_session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == seeded["document"].id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
        )
        assert len(chunks1) == 2
        ids1 = [c.id for c in chunks1]
        hashes1 = [c.chunk_hash for c in chunks1]
        items1 = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(items1) == 2
        assert all(i.source_weight == SOURCE_WEIGHT_DOCUMENT_CHUNK for i in items1)
        assert all(
            (i.metadata_json or {}).get("search_document_version")
            == DOCUMENT_CHUNK_SEARCH_DOCUMENT_VERSION
            for i in items1
        )

        # Second sync with identical source: ensure may skip if fingerprint COMPLETED;
        # force sync_document_group directly to verify row upsert identity.
        DocumentChunkSyncService(db_session).sync_document_group(seeded["group"].id)
        db_session.commit()
        chunks2 = list(
            db_session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == seeded["document"].id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
        )
        assert [c.id for c in chunks2] == ids1
        assert [c.chunk_hash for c in chunks2] == hashes1
        items2 = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(items2) == 2
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_reprocess_changed_content_clears_embedding(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk, DocumentPage
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session,
        pages=[(1, "원본 텍스트 A " * 20, "TEXT_PARSER")],
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        chunk = db_session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
        ).one()
        item = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                SearchIndexItem.object_id == chunk.id,
            )
        ).one()
        item.embedding = _vector()
        item.embedding_model = "bge-m3"
        item.embedding_version = "1"
        db_session.add(item)
        db_session.commit()
        old_id = chunk.id

        page = db_session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == seeded["document"].id)
        ).one()
        page.extracted_text = "변경된 텍스트 B " * 20
        seeded["document"].updated_at = datetime.now(UTC)
        db_session.add(page)
        db_session.add(seeded["document"])
        db_session.commit()

        job, outcome = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        assert outcome in {"created", "requeued"}
        SearchIndexService(db_session).process_job(job.id)

        chunk2 = db_session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
        ).one()
        assert chunk2.id == old_id
        assert "변경된" in chunk2.chunk_text
        item2 = db_session.get(SearchIndexItem, item.id)
        assert item2.search_text == chunk2.chunk_text
        assert item2.embedding is None
        embed_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        assert any(
            (j.payload_json or {}).get("operation") == "EMBED_SEARCH_INDEX_ITEM"
            for j in embed_jobs
        )
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_chunk_count_shrink_deactivates_trailing(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk, DocumentPage
    from app.db.models.search import SearchIndexItem
    from app.modules.document_processing.chunker import DOCUMENT_CHUNK_MAX_CHARS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    long_text = ("paragraph block\n\n" * 800)
    assert len(long_text) > DOCUMENT_CHUNK_MAX_CHARS * 3
    seeded = _seed_person_group(
        db_session, pages=[(1, long_text, "TEXT_PARSER")]
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
            ).all()
        )
        assert len(chunks) >= 5
        removed_ids = [c.id for c in sorted(chunks, key=lambda c: c.chunk_index)[3:]]

        page = db_session.scalars(
            select(DocumentPage).where(DocumentPage.document_id == seeded["document"].id)
        ).one()
        page.extracted_text = "short only"
        seeded["document"].updated_at = datetime.now(UTC)
        db_session.add(page)
        db_session.add(seeded["document"])
        db_session.commit()

        job, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        SearchIndexService(db_session).process_job(job.id)

        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 1
        remaining = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
            ).all()
        )
        assert len(remaining) == 1
        for rid in removed_ids:
            assert db_session.get(DocumentChunk, rid) is None
            dangling = list(
                db_session.scalars(
                    select(SearchIndexItem).where(
                        SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                        SearchIndexItem.object_id == rid,
                        SearchIndexItem.is_active.is_(True),
                    )
                ).all()
            )
            assert dangling == []
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_version_switch_and_delete_fallback(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "version one text", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        v1 = seeded["document"]
        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "version two text", "TEXT_PARSER")],
        )
        job, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        SearchIndexService(db_session).process_job(job.id)

        v2_chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == v2.id)
            ).all()
        )
        assert v2_chunks
        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert {i.object_id for i in active} == {c.id for c in v2_chunks}

        v1_chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == v1.id)
            ).all()
        )
        for c in v1_chunks:
            items = list(
                db_session.scalars(
                    select(SearchIndexItem).where(
                        SearchIndexItem.object_id == c.id,
                        SearchIndexItem.is_active.is_(True),
                    )
                ).all()
            )
            assert items == []

        # Soft-delete v2 → fallback to v1 READY (avoid dual is_latest unique violation)
        v2.deleted_at = datetime.now(UTC)
        v2.is_latest = False
        db_session.add(v2)
        db_session.flush()
        v1.is_latest = True
        db_session.add(v1)
        db_session.commit()
        job2, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        SearchIndexService(db_session).process_job(job2.id)

        active2 = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert {i.object_id for i in active2} == {c.id for c in v1_chunks}
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_processing_gap_keeps_previous_ready(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "ready v1", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        active_before = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert active_before

        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "processing v2", "TEXT_PARSER")],
            processing_status="PROCESSING",
        )
        # Sync while v2 PROCESSING — effective remains v1
        DocumentChunkSyncService(db_session).sync_document_group(seeded["group"].id)
        db_session.commit()
        active_mid = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert {i.id for i in active_mid} == {i.id for i in active_before}

        v2.processing_status = "FAILED"
        db_session.add(v2)
        db_session.commit()
        DocumentChunkSyncService(db_session).sync_document_group(seeded["group"].id)
        db_session.commit()
        active_fail = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert {i.id for i in active_fail} == {i.id for i in active_before}

        v2.processing_status = "READY"
        v2.updated_at = datetime.now(UTC)
        db_session.add(v2)
        db_session.commit()
        job, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        SearchIndexService(db_session).process_job(job.id)
        active_after = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert active_after
        assert all(i.search_text == "processing v2" for i in active_after)
        assert {i.id for i in active_before}.isdisjoint({i.id for i in active_after})
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_out_of_order_sync_jobs_use_live_state(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "v1 live text", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        job_v1, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()

        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "v2 live text", "TEXT_PARSER")],
        )
        job_v2, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        assert job_v1.id != job_v2.id

        # Process newer job first, then older v1 job.
        SearchIndexService(db_session).process_job(job_v2.id)
        SearchIndexService(db_session).process_job(job_v1.id)

        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 1
        assert active[0].search_text == "v2 live text"
        assert (active[0].metadata_json or {}).get("document_id") == str(v2.id)
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_person_deleted_deactivates_chunks(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService
    from app.modules.search.service import SearchIndexService

    admin = _create_user(db_session)
    seeded = _seed_person_group(
        db_session, pages=[(1, "person chunk", "TEXT_PARSER")]
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert active

        PeopleService(db_session).update_status(
            seeded["person"].id,
            PersonStatusUpdateRequest(status="DELETED"),
            admin.id,
        )
        # Process any PENDING chunk sync jobs created by status mutation.
        from app.db.models.search import SearchIndexJob

        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.status == "PENDING",
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        for job in pending:
            if (job.payload_json or {}).get("operation") == "SYNC_DOCUMENT_CHUNKS":
                SearchIndexService(db_session).process_job(job.id)

        inactive = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert inactive == []

        PeopleService(db_session).update_status(
            seeded["person"].id,
            PersonStatusUpdateRequest(status="ACTIVE"),
            admin.id,
        )
        pending2 = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.status == "PENDING",
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        for job in pending2:
            if (job.payload_json or {}).get("operation") == "SYNC_DOCUMENT_CHUNKS":
                SearchIndexService(db_session).process_job(job.id)
        restored = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(restored) == 1
        assert restored[0].search_text == "person chunk"
    finally:
        _cleanup_person(db_session, seeded["person"].id, user_id=admin.id)


def test_embedding_success_for_document_chunk(db_session, monkeypatch):
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "embedding chunk text", "TEXT_PARSER")]
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        item = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == seeded["person"].id,
                SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert item.embedding is None
        embed_job = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == seeded["person"].id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
                SearchIndexJob.object_type == "DOCUMENT_CHUNK",
            )
        ).first()
        assert embed_job is not None
        assert (embed_job.payload_json or {}).get("operation") == "EMBED_SEARCH_INDEX_ITEM"

        fake = _FakeProvider(_vector(fill=0.02))
        with patch(
            "app.ai.providers.embedding.get_embedding_provider", return_value=fake
        ):
            result = SearchIndexService(db_session).process_job(embed_job.id)
        assert result.status == "COMPLETED"
        db_session.refresh(item)
        assert item.embedding is not None
        assert item.embedding_model == "bge-m3"
        assert item.embedding_version is not None
        assert fake.calls and fake.calls[0] == "embedding chunk text"
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_document_chunk_scanner_and_old_version_exclusion(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.document_builder import content_hash
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "scanner chunk", "TEXT_PARSER")]
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        # Clear pending embed jobs from sync so scanner creates them.
        db_session.execute(
            delete(SearchIndexJob).where(
                SearchIndexJob.person_id == seeded["person"].id,
                SearchIndexJob.action == "UPSERT",
            )
        )
        db_session.commit()

        active = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == seeded["person"].id,
                SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert active.embedding is None

        inactive = SearchIndexItem(
            id=uuid.uuid4(),
            person_id=seeded["person"].id,
            object_type="DOCUMENT_CHUNK",
            object_id=uuid.uuid4(),
            search_text="old version chunk",
            embedding=None,
            source_weight=Decimal("0.700"),
            metadata_json={"content_hash": content_hash("old version chunk")},
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        first = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        second = SearchIndexService(db_session).enqueue_missing_embeddings(limit=50)
        assert first["enqueued"] >= 1
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.status == "PENDING",
                )
            ).all()
        )
        item_ids = {j.payload_json.get("search_index_item_id") for j in pending}
        assert str(active.id) in item_ids
        assert str(inactive.id) not in item_ids
        assert second["enqueued"] == 0
        # Ensure DocumentChunk ownership chain exists for active item
        chunk = db_session.get(DocumentChunk, active.object_id)
        assert chunk is not None
        assert chunk.document_id == seeded["document"].id
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_zero_text_image_document_no_beat_loop(db_session, monkeypatch):
    from app.db.models.document import DocumentChunk
    from app.db.models.search import SearchIndexItem
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session,
        pages=[(1, None, None)],
    )
    try:
        _enable_embedding(monkeypatch)
        job, outcome = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        assert outcome == "created"
        result = SearchIndexService(db_session).process_job(job.id)
        assert result.status == "COMPLETED"

        chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
            ).all()
        )
        assert chunks == []
        items = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                )
            ).all()
        )
        assert items == []

        # Beat-equivalent enqueue must not recreate jobs for completed zero-chunk sync.
        from app.db.models.search import SearchIndexJob

        needed = DocumentChunkSyncService(db_session).list_groups_needing_chunk_sync(
            limit=500
        )
        assert seeded["group"].id not in needed
        before_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.object_type == "DOCUMENT_CHUNK",
                )
            ).all()
        )
        SearchIndexService(db_session).enqueue_missing_document_chunk_syncs(limit=100)
        after_jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.object_type == "DOCUMENT_CHUNK",
                )
            ).all()
        )
        assert len(after_jobs) == len(before_jobs)

        # Document stays READY
        db_session.refresh(seeded["document"])
        assert seeded["document"].processing_status == "READY"
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_backfill_starvation_skips_already_synced(db_session, monkeypatch):
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    synced = []
    missing = None
    try:
        _enable_embedding(monkeypatch)
        # Create several already-synced groups (lower UUIDs processed first via id order
        # is not guaranteed; we sync them so fingerprint COMPLETED, then create missing).
        for _ in range(3):
            s = _seed_person_group(
                db_session, pages=[(1, f"synced {_}", "TEXT_PARSER")]
            )
            synced.append(s)
            _sync_group(db_session, s["group"].id, monkeypatch)

        missing = _seed_person_group(
            db_session, pages=[(1, "needs sync", "TEXT_PARSER")]
        )
        needed = DocumentChunkSyncService(db_session).list_groups_needing_chunk_sync(
            limit=10
        )
        assert missing["group"].id in needed
        # Already synced groups should not appear for current fingerprint
        for s in synced:
            assert s["group"].id not in needed

        enq = SearchIndexService(db_session).enqueue_missing_document_chunk_syncs(limit=10)
        assert enq["enqueued"] >= 1
    finally:
        if missing is not None:
            _cleanup_person(db_session, missing["person"].id)
        for s in synced:
            _cleanup_person(db_session, s["person"].id)


def test_ready_hook_ensures_sync_job_only(db_session, monkeypatch):
    """Document READY path creates SearchIndexJob without materializing chunks inline."""
    from app.db.models.document import DocumentChunk
    from app.db.models.search import SearchIndexJob
    from app.modules.document_processing.types import ExtractedPage, ExtractionResult
    from app.modules.document_processing.repository import DocumentProcessingRepository
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS

    seeded = _seed_person_group(
        db_session,
        pages=[],
        processing_status="PROCESSING",
    )
    try:
        repo = DocumentProcessingRepository(db_session)
        repo.replace_pages(
            seeded["document"].id,
            [
                ExtractedPage(
                    page_no=1,
                    extracted_text="hook text",
                    layout_json={},
                    extraction_method="TEXT_PARSER",
                )
            ],
        )
        repo.mark_ready(
            seeded["document"],
            preview_storage_key=None,
            preview_page_count=1,
        )
        from app.modules.search.document_chunk_sync import DocumentChunkSyncService

        DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()

        chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == seeded["document"].id)
            ).all()
        )
        assert chunks == []
        jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == seeded["person"].id,
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        assert any(
            (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS
            for j in jobs
        )
        assert all(j.object_id == seeded["group"].id for j in jobs if (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS)
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_concurrent_ensure_sync_job_one_row(db_session, monkeypatch):
    """Two sessions racing ensure_sync_job must yield exactly one job row."""
    import threading

    from app.db.models.search import SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService

    seeded = _seed_person_group(
        db_session, pages=[(1, "race text", "TEXT_PARSER")]
    )
    group_id = seeded["group"].id
    person_id = seeded["person"].id
    try:
        _enable_embedding(monkeypatch)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        job_ids: list = []
        errors: list[BaseException] = []
        commits_ok: list[bool] = []

        def worker() -> None:
            s = SessionLocal()
            try:
                barrier.wait(timeout=5)
                job, outcome = DocumentChunkSyncService(s).ensure_sync_job(group_id)
                assert job is not None
                s.commit()
                outcomes.append(outcome)
                job_ids.append(job.id)
                commits_ok.append(True)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    s.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                s.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        assert errors == []
        assert commits_ok == [True, True]
        assert len(outcomes) == 2
        assert outcomes.count("created") == 1
        assert any(o in {"already_pending", "created"} for o in outcomes)
        assert len(set(job_ids)) == 1

        jobs = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person_id,
                    SearchIndexJob.action == "UPSERT",
                    SearchIndexJob.object_type == "DOCUMENT_CHUNK",
                )
            ).all()
        )
        sync_jobs = [
            j
            for j in jobs
            if (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS
        ]
        assert len(sync_jobs) == 1
        assert sync_jobs[0].idempotency_key
        assert {j.idempotency_key for j in sync_jobs} == {sync_jobs[0].idempotency_key}
    finally:
        _cleanup_person(db_session, person_id)


def test_source_mutation_waits_for_sync_group_lock(db_session, monkeypatch):
    """v2 READY+ensure cannot commit while old sync holds DocumentGroup lock."""
    import threading
    import time

    from app.db.models.document import Document
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "version one text", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        v1 = seeded["document"]
        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "version two text", "TEXT_PARSER")],
            processing_status="PROCESSING",
        )
        group_id = seeded["group"].id
        person_id = seeded["person"].id

        # Fresh PENDING sync job that will still see v1 until v2 becomes READY.
        job_v1, outcome = DocumentChunkSyncService(db_session).ensure_sync_job(group_id)
        assert job_v1 is not None
        if outcome == "already_completed" or job_v1.status != "PENDING":
            # Force a live re-process under Group lock for the race window.
            job_v1.status = "PENDING"
            job_v1.started_at = None
            job_v1.completed_at = None
            job_v1.error_message = None
            db_session.add(job_v1)
        db_session.commit()
        job_v1_id = job_v1.id
        assert db_session.get(type(job_v1), job_v1_id).status == "PENDING"

        entered = threading.Event()
        release = threading.Event()
        mutation_done = threading.Event()
        mutation_started = threading.Event()
        errors: list[BaseException] = []
        seen_effective: list[int] = []

        def after_locks(self, *, person, group, effective):
            if effective is not None:
                seen_effective.append(int(effective.version_no))
            entered.set()
            assert release.wait(timeout=10)

        monkeypatch.setattr(
            DocumentChunkSyncService, "_after_source_locks", after_locks
        )

        def sync_worker() -> None:
            db = SessionLocal()
            try:
                SearchIndexService(db).process_job(job_v1_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        def mutation_worker() -> None:
            db = SessionLocal()
            try:
                assert entered.wait(timeout=10)
                mutation_started.set()
                doc = db.get(Document, v2.id)
                assert doc is not None
                doc.processing_status = "READY"
                doc.updated_at = datetime.now(UTC)
                db.add(doc)
                DocumentChunkSyncService(db).ensure_sync_job(group_id)
                db.commit()
                mutation_done.set()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

        t_sync = threading.Thread(target=sync_worker)
        t_mut = threading.Thread(target=mutation_worker)
        t_sync.start()
        assert entered.wait(timeout=10)
        t_mut.start()
        assert mutation_started.wait(timeout=5)
        # While sync holds Group lock, mutation must not finish ensure+commit.
        time.sleep(0.4)
        assert not mutation_done.is_set()
        db_session.expire_all()
        still = db_session.get(Document, v2.id)
        assert still is not None
        assert still.processing_status == "PROCESSING"

        release.set()
        t_sync.join(timeout=15)
        t_mut.join(timeout=15)
        assert errors == []
        assert mutation_done.is_set()
        assert seen_effective and seen_effective[0] == 1

        db_session.expire_all()
        v2_row = db_session.get(Document, v2.id)
        assert v2_row is not None and v2_row.processing_status == "READY"

        # Process the v2 sync job created by mutation.
        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person_id,
                    SearchIndexJob.status == "PENDING",
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        for job in pending:
            if (job.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS:
                SearchIndexService(db_session).process_job(job.id)

        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person_id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 1
        assert active[0].search_text == "version two text"
        assert (active[0].metadata_json or {}).get("document_id") == str(v2.id)
        assert (active[0].metadata_json or {}).get("document_version_no") == 2
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_concurrent_different_fingerprint_sync_jobs_serialize(db_session, monkeypatch):
    """Two SYNC jobs for the same group serialize on Group lock; final=live effective."""
    import threading

    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "v1 live text", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        job_v1, _ = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "v2 live text", "TEXT_PARSER")],
        )
        job_v2, _ = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        assert job_v1 is not None and job_v2 is not None
        assert job_v1.id != job_v2.id
        job_ids = [job_v1.id, job_v2.id]

        in_critical = 0
        max_in_critical = 0
        lock = threading.Lock()
        errors: list[BaseException] = []
        started = threading.Event()

        def after_locks(self, *, person, group, effective):
            nonlocal in_critical, max_in_critical
            import time

            with lock:
                in_critical += 1
                max_in_critical = max(max_in_critical, in_critical)
            started.set()
            try:
                # Hold critical section so a concurrent peer would overlap
                # without Group serialization.
                time.sleep(0.25)
            finally:
                with lock:
                    in_critical -= 1

        monkeypatch.setattr(
            DocumentChunkSyncService, "_after_source_locks", after_locks
        )

        def worker(jid) -> None:
            db = SessionLocal()
            try:
                SearchIndexService(db).process_job(jid)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        t1 = threading.Thread(target=worker, args=(job_ids[0],))
        t2 = threading.Thread(target=worker, args=(job_ids[1],))
        t1.start()
        t2.start()
        t1.join(timeout=20)
        t2.join(timeout=20)
        assert errors == []
        # Group lock: materialization critical section must not overlap.
        assert max_in_critical == 1
        assert started.is_set()

        db_session.expire_all()
        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(active) == 1
        assert active[0].search_text == "v2 live text"
        assert (active[0].metadata_json or {}).get("document_id") == str(v2.id)

        terminals = list(
            db_session.scalars(
                select(SearchIndexJob).where(SearchIndexJob.id.in_(job_ids))
            ).all()
        )
        assert all(j.status == "COMPLETED" for j in terminals)
        assert all(
            (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS
            for j in terminals
        )
    finally:
        _cleanup_person(db_session, seeded["person"].id)


def test_person_delete_waits_for_sync_person_lock(db_session, monkeypatch):
    """Person DELETE cannot commit while ACTIVE sync holds Person FOR UPDATE."""
    import threading
    import time

    from app.db.models.person import Person
    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.db.session import SessionLocal
    from app.modules.people.schemas import PersonStatusUpdateRequest
    from app.modules.people.service import PeopleService
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    admin = _create_user(db_session)
    seeded = _seed_person_group(
        db_session, pages=[(1, "person chunk", "TEXT_PARSER")]
    )
    try:
        _enable_embedding(monkeypatch)
        job, _ = DocumentChunkSyncService(db_session).ensure_sync_job(seeded["group"].id)
        db_session.commit()
        assert job is not None
        job_id = job.id
        person_id = seeded["person"].id

        entered = threading.Event()
        release = threading.Event()
        delete_done = threading.Event()
        delete_started = threading.Event()
        errors: list[BaseException] = []

        def after_locks(self, *, person, group, effective):
            entered.set()
            assert release.wait(timeout=10)

        monkeypatch.setattr(
            DocumentChunkSyncService, "_after_source_locks", after_locks
        )

        def sync_worker() -> None:
            db = SessionLocal()
            try:
                SearchIndexService(db).process_job(job_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        def delete_worker() -> None:
            db = SessionLocal()
            try:
                assert entered.wait(timeout=10)
                delete_started.set()
                PeopleService(db).update_status(
                    person_id,
                    PersonStatusUpdateRequest(status="DELETED"),
                    admin.id,
                )
                delete_done.set()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                db.close()

        t_sync = threading.Thread(target=sync_worker)
        t_del = threading.Thread(target=delete_worker)
        t_sync.start()
        assert entered.wait(timeout=10)
        t_del.start()
        assert delete_started.wait(timeout=5)
        time.sleep(0.4)
        assert not delete_done.is_set()
        db_session.expire_all()
        person_row = db_session.get(Person, person_id)
        assert person_row is not None
        assert person_row.status == "ACTIVE"

        release.set()
        t_sync.join(timeout=15)
        t_del.join(timeout=15)
        assert errors == []
        assert delete_done.is_set()

        db_session.expire_all()
        assert db_session.get(Person, person_id).status == "DELETED"

        pending = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person_id,
                    SearchIndexJob.status == "PENDING",
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        for j in pending:
            if (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS:
                SearchIndexService(db_session).process_job(j.id)

        active = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person_id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert active == []

        # Restore + sync → current READY chunks active again.
        PeopleService(db_session).update_status(
            person_id,
            PersonStatusUpdateRequest(status="ACTIVE"),
            admin.id,
        )
        pending2 = list(
            db_session.scalars(
                select(SearchIndexJob).where(
                    SearchIndexJob.person_id == person_id,
                    SearchIndexJob.status == "PENDING",
                    SearchIndexJob.action == "UPSERT",
                )
            ).all()
        )
        for j in pending2:
            if (j.payload_json or {}).get("operation") == OPERATION_SYNC_DOCUMENT_CHUNKS:
                SearchIndexService(db_session).process_job(j.id)
        restored = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == person_id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(restored) == 1
        assert restored[0].search_text == "person chunk"
    finally:
        _cleanup_person(db_session, seeded["person"].id, user_id=admin.id)


def test_version_fallback_requeues_stale_completed_embedding_job(
    db_session, monkeypatch
):
    """Inactive stale COMPLETED embed job is requeued when v1 item is reactivated."""
    from unittest.mock import patch

    from app.db.models.search import SearchIndexItem, SearchIndexJob
    from app.modules.search.document_chunk_policy import OPERATION_SYNC_DOCUMENT_CHUNKS
    from app.modules.search.document_chunk_sync import DocumentChunkSyncService
    from app.modules.search.service import SearchIndexService

    seeded = _seed_person_group(
        db_session, pages=[(1, "version one embed text", "TEXT_PARSER")], version_no=1
    )
    try:
        _enable_embedding(monkeypatch)
        _sync_group(db_session, seeded["group"].id, monkeypatch)
        v1 = seeded["document"]
        v1_item = db_session.scalars(
            select(SearchIndexItem).where(
                SearchIndexItem.person_id == seeded["person"].id,
                SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                SearchIndexItem.is_active.is_(True),
            )
        ).one()
        assert v1_item.embedding is None
        j1 = db_session.scalars(
            select(SearchIndexJob).where(
                SearchIndexJob.person_id == seeded["person"].id,
                SearchIndexJob.action == "UPSERT",
                SearchIndexJob.status == "PENDING",
                SearchIndexJob.object_type == "DOCUMENT_CHUNK",
            )
        ).first()
        assert j1 is not None
        assert (j1.payload_json or {}).get("operation") == "EMBED_SEARCH_INDEX_ITEM"
        assert (j1.payload_json or {}).get("search_index_item_id") == str(v1_item.id)
        j1_id = j1.id
        j1_key = j1.idempotency_key
        v1_item_id = v1_item.id

        v2 = _add_document_version(
            db_session,
            seeded["group"],
            version_no=2,
            pages=[(1, "version two embed text", "TEXT_PARSER")],
        )
        job_sync, _ = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        SearchIndexService(db_session).process_job(job_sync.id)

        db_session.refresh(v1_item)
        assert v1_item.is_active is False
        v2_items = list(
            db_session.scalars(
                select(SearchIndexItem).where(
                    SearchIndexItem.person_id == seeded["person"].id,
                    SearchIndexItem.object_type == "DOCUMENT_CHUNK",
                    SearchIndexItem.is_active.is_(True),
                )
            ).all()
        )
        assert len(v2_items) == 1
        assert v2_items[0].search_text == "version two embed text"

        # Late J1 run against inactive v1 → COMPLETED stale no-op (no provider).
        fake_idle = _FakeProvider()
        with patch(
            "app.ai.providers.embedding.get_embedding_provider", return_value=fake_idle
        ):
            result = SearchIndexService(db_session).process_job(j1_id)
        assert result.status == "COMPLETED"
        assert fake_idle.calls == []
        db_session.refresh(v1_item)
        assert v1_item.embedding is None

        # Soft-delete v2 → v1 fallback sync reactivates same SearchIndexItem id.
        v2.deleted_at = datetime.now(UTC)
        v2.is_latest = False
        db_session.add(v2)
        db_session.flush()
        v1.is_latest = True
        db_session.add(v1)
        db_session.commit()
        job_fb, _ = DocumentChunkSyncService(db_session).ensure_sync_job(
            seeded["group"].id
        )
        db_session.commit()
        SearchIndexService(db_session).process_job(job_fb.id)

        db_session.expire_all()
        reactivated = db_session.get(SearchIndexItem, v1_item_id)
        assert reactivated is not None
        assert reactivated.is_active is True
        assert reactivated.embedding is None
        assert reactivated.search_text == "version one embed text"

        jobs_same_key = list(
            db_session.scalars(
                select(SearchIndexJob).where(SearchIndexJob.idempotency_key == j1_key)
            ).all()
        )
        assert len(jobs_same_key) == 1
        assert jobs_same_key[0].id == j1_id
        assert jobs_same_key[0].status == "PENDING"

        fake = _FakeProvider(_vector(fill=0.19))
        with patch(
            "app.ai.providers.embedding.get_embedding_provider", return_value=fake
        ):
            result2 = SearchIndexService(db_session).process_job(j1_id)
        assert result2.status == "COMPLETED"
        db_session.refresh(reactivated)
        assert reactivated.embedding is not None
        assert reactivated.embedding_model == "bge-m3"
        assert reactivated.embedding_version is not None
        assert fake.calls and fake.calls[0] == "version one embed text"
    finally:
        _cleanup_person(db_session, seeded["person"].id)
