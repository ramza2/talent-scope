"""LLM provider protocol and OpenAI-compatible implementation."""

from __future__ import annotations

from typing import Protocol

from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.openai_compat import (
    extract_message_content,
    post_chat_completions,
)
from app.ai.schemas.identity import IdentityExtraction
from app.ai.utils.json_extract import parse_json_object
from app.core.config import Settings, get_settings


class LLMProvider(Protocol):
    def extract_identity(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> IdentityExtraction: ...

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> dict: ...


class OpenAICompatibleLLMProvider:
    """Chat Completions LLM for identity and profile extraction."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract_identity(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> IdentityExtraction:
        content = self._chat(system_prompt, user_prompt, log_context)
        return _parse_identity_content(content, allow_repair=True)

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> dict:
        content = self._chat(system_prompt, user_prompt, log_context)
        return _parse_json_dict(content, allow_repair=True)

    def _chat(
        self, system_prompt: str, user_prompt: str, log_context: dict | None
    ) -> str:
        timeout = float(self.settings.ai_request_timeout_seconds)
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        ctx = {
            **(log_context or {}),
            "provider": "llm",
            "input_char_count": len(system_prompt) + len(user_prompt),
        }
        data = post_chat_completions(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.llm_api_key,
            payload=payload,
            timeout_seconds=timeout,
            log_context=ctx,
        )
        return extract_message_content(data)


def _parse_json_dict(content: str, *, allow_repair: bool) -> dict:
    try:
        obj = parse_json_object(content)
        if not isinstance(obj, dict):
            raise AIResponseValidationError("AI JSON root is not an object")
        return obj
    except (AIResponseValidationError, ValueError, TypeError) as first_exc:
        if not allow_repair:
            raise AIResponseValidationError("JSON validation failed") from first_exc
        try:
            obj = parse_json_object(content, aggressive=True)
            if not isinstance(obj, dict):
                raise AIResponseValidationError("AI JSON root is not an object")
            return obj
        except Exception as exc:
            raise AIResponseValidationError("JSON validation failed") from exc


def _parse_identity_content(
    content: str, *, allow_repair: bool
) -> IdentityExtraction:
    obj = _parse_json_dict(content, allow_repair=allow_repair)
    try:
        return IdentityExtraction.model_validate(obj)
    except Exception as exc:
        raise AIResponseValidationError("identity JSON validation failed") from exc


class FakeLLMProvider:
    """Deterministic LLM for tests — never calls the network."""

    def __init__(
        self,
        identity: IdentityExtraction | dict | None = None,
        *,
        raw_content: str | None = None,
        fail: bool = False,
        fail_validation: bool = False,
        profile_json: dict | None = None,
    ) -> None:
        if isinstance(identity, dict):
            self.identity = IdentityExtraction.model_validate(identity)
        else:
            self.identity = identity or IdentityExtraction(
                name="홍길동",
                company="ABC테크",
                phone="010-1234-5678",
                email="hong@example.com",
            )
        self.raw_content = raw_content
        self.fail = fail
        self.fail_validation = fail_validation
        self.profile_json = profile_json
        self.calls = 0
        self.last_system_prompt: str | None = None
        self.last_user_prompt: str | None = None

    def extract_identity(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> IdentityExtraction:
        self.calls += 1
        _ = (system_prompt, user_prompt, log_context)
        if self.fail:
            raise AIProviderError("injected LLM failure")
        if self.fail_validation:
            raise AIResponseValidationError("injected validation failure")
        if self.raw_content is not None:
            return _parse_identity_content(self.raw_content, allow_repair=True)
        return self.identity.model_copy()

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        log_context: dict | None = None,
    ) -> dict:
        self.calls += 1
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        _ = log_context
        if self.fail:
            raise AIProviderError("injected LLM failure")
        if self.fail_validation:
            raise AIResponseValidationError("injected validation failure")
        if self.raw_content is not None:
            return _parse_json_dict(self.raw_content, allow_repair=True)
        if self.profile_json is not None:
            return dict(self.profile_json)
        return {
            "schema_version": "profile-candidate-v1",
            "profile": {"name": self.identity.name},
            "jobs": [],
            "skills": [],
            "expertise": [],
            "employment_history": [],
            "education": [],
            "certifications": [],
            "projects": [],
            "summary": {},
            "analysis": {"overall_confidence": 0.8},
        }
