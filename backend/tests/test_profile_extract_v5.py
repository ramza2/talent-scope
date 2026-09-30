"""profile-extract-v5 compact prompt + finish_reason=length handling."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient

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


def test_omitted_optional_fields_validate_as_profile_candidate() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.normalize import normalize_candidate

    compact = {
        "schema_version": "profile-candidate-v1",
        "profile": {
            "name": "홍길동",
            "technical_grade": "EXPERT",
            "source_refs": {
                "name": [
                    {
                        "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "page_no": 1,
                        "quote_text": "홍길동",
                    }
                ]
            },
        },
        "jobs": [{"raw_value": "AI개발", "job_type": "PRIMARY"}],
        "skills": [{"raw_value": "Python"}],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [{"project_name": "공공 RAG"}],
        "summary": {},
        "analysis": {"overall_confidence": 0.8},
    }
    doc = ProfileCandidateDocument.model_validate(compact)
    assert doc.profile.name == "홍길동"
    assert doc.profile.phone is None
    assert doc.profile.career_document_value is None
    assert "phone" not in doc.profile.source_refs
    assert doc.jobs[0].code is None
    assert doc.projects[0].skills == []

    normalized = normalize_candidate(
        compact,
        catalog={},
        allowed_documents={"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {1}},
        page_texts={("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 1): "홍길동 이력서"},
    )
    assert normalized.profile.name == "홍길동"
    assert normalized.skills[0].raw_value == "Python"


def test_finish_reason_length_is_not_parsed_as_candidate(monkeypatch) -> None:
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings

    truncated_body = (
        '{"schema_version":"profile-candidate-v1","education":[{"school_name":"서울대",'
        '"major":"컴공","degree":"학사"'
    )

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            200,
            json={
                "model": "Qwen3-14B",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"role": "assistant", "content": truncated_body},
                    }
                ],
                "usage": {
                    "prompt_tokens": 4256,
                    "completion_tokens": 3936,
                    "total_tokens": 8192,
                },
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    provider = OpenAICompatibleLLMProvider(
        Settings(
            llm_base_url="https://llm.example.test",
            llm_api_key="",
            llm_model="Qwen3-14B",
            analysis_ai_request_timeout_seconds=30,
        )
    )
    with pytest.raises(AIResponseTruncatedError) as exc_info:
        provider.complete_json(system_prompt="s", user_prompt="u")
    assert exc_info.value.meta["finish_reason"] == "length"
    assert exc_info.value.meta["total_tokens"] == 8192
    # Content must not appear in exception metadata.
    assert "서울대" not in str(exc_info.value.meta)
    assert truncated_body not in str(exc_info.value)


def test_length_triggers_recovery_retry_then_reviewing(db_session):
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.modules.analysis.service import AnalysisService
    from app.storage.s3 import get_object_storage
    from tests.test_analysis import (
        _RICH_PAGE_TEXT,
        _cleanup_person,
        _create_user,
        _queue_run,
        _seed_person_doc,
        _valid_candidate_json,
    )

    class _LengthThenValidLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.user_prompts: list[str] = []

        def complete_json(self, **kwargs):
            self.calls += 1
            prompt = kwargs.get("user_prompt")
            if isinstance(prompt, str):
                self.user_prompts.append(prompt)
            if self.calls == 1:
                raise AIResponseTruncatedError(
                    meta={"finish_reason": "length", "total_tokens": 8192}
                )
            return dict(_valid_candidate_json())

    admin = _create_user(
        db_session, login_id=f"v5_{uuid.uuid4().hex[:10]}", password="Passw0rd!"
    )
    person, document = _seed_person_doc(
        db_session,
        admin.id,
        doc_type_code="DOC-RESUME",
        doc_type_name="이력서",
        page_text=_RICH_PAGE_TEXT,
    )
    run = _queue_run(db_session, person.id, document.id)
    llm = _LengthThenValidLLM()
    service = AnalysisService(db_session, storage=get_object_storage(), llm=llm)
    assert service.run_analysis(run.id) == "REVIEWING"
    assert llm.calls == 2
    assert "[RECOVERY]" not in llm.user_prompts[0]
    assert llm.user_prompts[1].startswith("[RECOVERY]")
    db_session.refresh(run)
    assert run.status == "REVIEWING"
    _cleanup_person(db_session, person.id, admin.id)


def test_stop_finish_reason_still_parses_json(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            200,
            json={
                "model": "Qwen3-14B",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"schema_version":"profile-candidate-v1",'
                                '"profile":{"name":"홍길동"},'
                                '"jobs":[],"skills":[],"expertise":[],'
                                '"employment_history":[],"education":[],'
                                '"certifications":[],"projects":[],'
                                '"summary":{},"analysis":{}}'
                            )
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    provider = OpenAICompatibleLLMProvider(
        Settings(
            llm_base_url="https://llm.example.test",
            llm_api_key="",
            llm_model="Qwen3-14B",
            analysis_ai_request_timeout_seconds=30,
        )
    )
    data = provider.complete_json(system_prompt="s", user_prompt="u")
    assert data["profile"]["name"] == "홍길동"
