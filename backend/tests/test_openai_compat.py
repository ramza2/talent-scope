"""Focused tests for shared OpenAI-compatible HTTP helpers."""

from __future__ import annotations

import httpx
import pytest

from app.ai.providers.openai_compat import post_chat_completions, post_embeddings


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
