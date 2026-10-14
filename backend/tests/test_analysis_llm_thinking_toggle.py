"""ANALYSIS_LLM_ENABLE_THINKING applies only to complete_json payloads."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


def _capture_payloads(monkeypatch):
    import app.ai.providers.llm as llm_module

    observed: list[dict] = []

    def fake_post_chat_completions(*, payload, **kwargs):
        observed.append(dict(payload))
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true, "name": "홍길동"}'},
                }
            ],
            "model": payload.get("model"),
            "usage": {},
        }

    monkeypatch.setattr(llm_module, "post_chat_completions", fake_post_chat_completions)
    return observed


def _settings(**kwargs):
    from app.core.config import Settings

    base = dict(
        llm_base_url="https://llm.example.test",
        llm_api_key="test-key",
        llm_model="qwen3-test",
        ai_request_timeout_seconds=30,
        analysis_ai_request_timeout_seconds=60,
    )
    base.update(kwargs)
    return Settings(**base)


def test_complete_json_omits_thinking_kwargs_when_unset(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider

    observed = _capture_payloads(monkeypatch)
    provider = OpenAICompatibleLLMProvider(_settings(analysis_llm_enable_thinking=None))
    provider.complete_json(system_prompt="s", user_prompt="u")
    assert len(observed) == 1
    assert "chat_template_kwargs" not in observed[0]


def test_complete_json_passes_enable_thinking_false(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider

    observed = _capture_payloads(monkeypatch)
    provider = OpenAICompatibleLLMProvider(_settings(analysis_llm_enable_thinking=False))
    provider.complete_json(system_prompt="s", user_prompt="u")
    assert observed[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_complete_json_passes_enable_thinking_true(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider

    observed = _capture_payloads(monkeypatch)
    provider = OpenAICompatibleLLMProvider(_settings(analysis_llm_enable_thinking=True))
    provider.complete_json(system_prompt="s", user_prompt="u")
    assert observed[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_extract_identity_never_includes_thinking_toggle(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider

    observed = _capture_payloads(monkeypatch)
    provider = OpenAICompatibleLLMProvider(_settings(analysis_llm_enable_thinking=False))
    provider.extract_identity(system_prompt="s", user_prompt="identity")
    assert len(observed) == 1
    assert "chat_template_kwargs" not in observed[0]

    observed.clear()
    provider = OpenAICompatibleLLMProvider(_settings(analysis_llm_enable_thinking=True))
    provider.extract_identity(system_prompt="s", user_prompt="identity")
    assert "chat_template_kwargs" not in observed[0]
