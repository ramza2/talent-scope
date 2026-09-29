"""Focused tests for shared OpenAI-compatible HTTP helpers."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.ai.providers.openai_compat import (
    completion_response_meta,
    post_chat_completions,
    post_embeddings,
)


@pytest.mark.parametrize("api_key", ["", "   ", "change-me", "  change-me  "])
def test_chat_completions_omits_authorization_for_optional_api_key(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        captured["json"] = json
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    data = post_chat_completions(
        base_url="https://llm.example.test",
        api_key=api_key,
        payload={"model": "test-model", "messages": []},
        timeout_seconds=1,
    )

    assert data["choices"][0]["message"]["content"] == "ok"
    assert captured["url"] == "https://llm.example.test/v1/chat/completions"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_chat_completions_sends_trimmed_bearer_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        captured["headers"] = dict(headers or {})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    post_chat_completions(
        base_url="https://llm.example.test/v1",
        api_key="  secret-token  ",
        payload={"model": "test-model", "messages": []},
        timeout_seconds=1,
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer secret-token"


def test_completion_response_meta_extracts_finish_reason_and_usage() -> None:
    meta = completion_response_meta(
        {
            "model": "Qwen3-14B",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": "SECRET-CONTENT-SHOULD-NOT-APPEAR",
                        "tool_calls": [{"id": "call_1"}],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 4096,
                "total_tokens": 5296,
            },
        },
        fallback_model="fallback-model",
    )
    assert meta == {
        "model": "Qwen3-14B",
        "finish_reason": "length",
        "prompt_tokens": 1200,
        "completion_tokens": 4096,
        "total_tokens": 5296,
    }
    assert "SECRET" not in str(meta)


def test_completion_response_meta_tolerates_missing_usage_and_choices() -> None:
    meta = completion_response_meta({"id": "resp-1"}, fallback_model="req-model")
    assert meta["model"] == "req-model"
    assert meta["finish_reason"] is None
    assert meta["prompt_tokens"] is None
    assert meta["completion_tokens"] is None
    assert meta["total_tokens"] is None

    partial = completion_response_meta(
        {
            "choices": [{"finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10},
        },
        fallback_model="m",
    )
    assert partial["finish_reason"] == "stop"
    assert partial["prompt_tokens"] == 10
    assert partial["completion_tokens"] is None
    assert partial["total_tokens"] is None


def test_chat_completions_logs_finish_reason_usage_not_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_content = "SECRET-ASSISTANT-CONTENT-XYZ"
    secret_tool = "SECRET-TOOL-CALL-PAYLOAD"

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            200,
            json={
                "model": "diag-model",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": secret_content,
                            "tool_calls": [{"function": {"arguments": secret_tool}}],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 800,
                    "completion_tokens": 2048,
                    "total_tokens": 2848,
                },
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with caplog.at_level(logging.INFO, logger="app.ai.providers.openai_compat"):
        data = post_chat_completions(
            base_url="https://llm.example.test",
            api_key="",
            payload={"model": "diag-model", "messages": [{"role": "user", "content": "hi"}]},
            timeout_seconds=1,
            log_context={"analysis_run_id": "run-1", "attempt": 2},
        )

    assert data["choices"][0]["message"]["content"] == secret_content
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("ai_request model=diag-model http_status=200" in m for m in messages)
    meta_logs = [m for m in messages if "ai_completion_meta" in m]
    assert meta_logs
    meta_msg = meta_logs[0]
    assert "finish_reason=length" in meta_msg
    assert "prompt_tokens=800" in meta_msg
    assert "completion_tokens=2048" in meta_msg
    assert "total_tokens=2848" in meta_msg
    assert "model=diag-model" in meta_msg
    assert "analysis_run_id" in meta_msg
    assert secret_content not in meta_msg
    assert secret_tool not in meta_msg
    for message in messages:
        assert secret_content not in message
        assert secret_tool not in message


def test_chat_completions_logs_meta_when_usage_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with caplog.at_level(logging.INFO, logger="app.ai.providers.openai_compat"):
        data = post_chat_completions(
            base_url="https://llm.example.test",
            api_key="",
            payload={"model": "no-usage-model", "messages": []},
            timeout_seconds=1,
            log_context={"provider": "llm"},
        )

    assert data["choices"][0]["finish_reason"] == "stop"
    meta_logs = [
        rec.getMessage()
        for rec in caplog.records
        if "ai_completion_meta" in rec.getMessage()
    ]
    assert meta_logs
    assert "finish_reason=stop" in meta_logs[0]
    assert "prompt_tokens=None" in meta_logs[0]
    assert "completion_tokens=None" in meta_logs[0]
    assert "total_tokens=None" in meta_logs[0]


@pytest.mark.parametrize("api_key", ["", "   ", "change-me"])
def test_embeddings_keep_same_optional_auth_semantics(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
) -> None:
    """The shared header builder must not regress the existing embedding behavior."""
    captured: dict[str, object] = {}

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        captured["headers"] = dict(headers or {})
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    post_embeddings(
        base_url="https://embedding.example.test",
        api_key=api_key,
        payload={"model": "bge-m3", "input": ["hello"]},
        timeout_seconds=1,
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "Authorization" not in headers
