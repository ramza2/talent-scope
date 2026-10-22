"""Focused contract tests for a shared OpenAI-compatible AI gateway."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.ai.providers.openai_compat import (
    normalize_chat_completions_url,
    normalize_embeddings_url,
    post_embeddings,
)
from app.ai.providers.vlm import OpenAICompatibleVLMProvider
from app.core.config import Settings


_GATEWAY = "https://gateway.example.test"


def test_shared_gateway_paths_are_openai_compatible() -> None:
    assert normalize_chat_completions_url(_GATEWAY) == (
        "https://gateway.example.test/v1/chat/completions"
    )
    assert normalize_embeddings_url(_GATEWAY) == (
        "https://gateway.example.test/v1/embeddings"
    )


def test_llm_gateway_uses_configured_alias_without_interface_change() -> None:
    captured: dict[str, object] = {}

    def fake_post_chat_completions(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"schema_version":"profile-candidate-v1"}'},
                }
            ]
        }

    settings = Settings(
        llm_base_url=_GATEWAY,
        llm_api_key="",
        llm_model="llm-alias",
    )
    provider = OpenAICompatibleLLMProvider(settings)

    with patch(
        "app.ai.providers.llm.post_chat_completions",
        side_effect=fake_post_chat_completions,
    ):
        result = provider.complete_json(system_prompt="system", user_prompt="user")

    assert result["schema_version"] == "profile-candidate-v1"
    assert captured["base_url"] == _GATEWAY
    assert captured["payload"]["model"] == "llm-alias"


def test_vlm_gateway_keeps_alias_and_image_url_payload() -> None:
    captured: dict[str, object] = {}

    def fake_post_chat_completions(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "visible text"},
                }
            ]
        }

    settings = Settings(
        vlm_base_url=_GATEWAY,
        vlm_api_key="",
        vlm_model="vlm-alias",
    )
    provider = OpenAICompatibleVLMProvider(settings)

    with patch(
        "app.ai.providers.vlm.post_chat_completions",
        side_effect=fake_post_chat_completions,
    ):
        text = provider.transcribe_image(
            image_bytes=b"fake-image",
            mime_type="image/png",
        )

    assert text == "visible text"
    assert captured["base_url"] == _GATEWAY
    payload = captured["payload"]
    assert payload["model"] == "vlm-alias"
    content = payload["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_embedding_gateway_keeps_alias_and_1024_dimensions(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.01] * 1024}],
                "model": "embedding-alias",
            },
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    settings = Settings(
        embedding_base_url=_GATEWAY,
        embedding_api_key="",
        embedding_model="embedding-alias",
        embedding_dimensions=1024,
    )

    data = post_embeddings(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        payload={
            "model": settings.embedding_model,
            "input": ["gateway compatibility"],
        },
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )

    assert settings.embedding_dimensions == 1024
    assert captured["url"] == "https://gateway.example.test/v1/embeddings"
    assert captured["payload"]["model"] == "embedding-alias"
    assert captured["payload"]["input"] == ["gateway compatibility"]
    assert len(data["data"][0]["embedding"]) == 1024


@pytest.mark.parametrize(
    ("client_key", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  test-talent-scope  ", "test-talent-scope"),
    ],
)
def test_modelops_client_identity_header_covers_all_gateway_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    client_key: str | None,
    expected: str | None,
) -> None:
    """Same optional ID on Chat (LLM/VLM), Embedding, count, capabilities."""
    from app.ai.providers.model_capabilities import fetch_model_capabilities
    from app.ai.providers.token_count import count_chat_prompt_tokens

    if client_key is None:
        monkeypatch.delenv("MODELOPS_CLIENT_KEY", raising=False)
    else:
        monkeypatch.setenv("MODELOPS_CLIENT_KEY", client_key)

    calls: list[tuple[str, dict[str, str], str | None]] = []

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        headers_copy = dict(headers or {})
        model = json.get("model") if isinstance(json, dict) else None
        calls.append((url, headers_copy, model))
        if url.endswith("/token-count"):
            return httpx.Response(
                200,
                json={
                    "status": "VERIFIED",
                    "model": model,
                    "prompt_tokens": 42,
                    "context_window": 8192,
                },
            )
        if url.endswith("/embeddings"):
            return httpx.Response(200, json={"data": []})
        content = "visible text" if model == "vlm-alias" else '{"status":"ok"}'
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        )

    def fake_get(self, url, *, headers=None, **kwargs):
        calls.append((url, dict(headers or {}), None))
        return httpx.Response(
            200,
            json={"status": "VERIFIED", "id": "llm-alias", "context_window": 8192},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(httpx.Client, "get", fake_get)

    settings = Settings(
        llm_base_url=_GATEWAY,
        llm_api_key="api-auth",
        llm_model="llm-alias",
        vlm_base_url=_GATEWAY,
        vlm_api_key="api-auth",
        vlm_model="vlm-alias",
    )

    assert OpenAICompatibleLLMProvider(settings).complete_json(
        system_prompt="system", user_prompt="user"
    ) == {"status": "ok"}
    assert OpenAICompatibleVLMProvider(settings).transcribe_image(
        image_bytes=b"fake-image"
    ) == "visible text"
    assert post_embeddings(
        base_url=_GATEWAY,
        api_key="api-auth",
        payload={"model": "bge-m3", "input": ["text"]},
        timeout_seconds=1,
    ) == {"data": []}
    count = count_chat_prompt_tokens(
        settings,
        system_prompt="system",
        user_prompt="user",
        enable_thinking=False,
    )
    assert count.prompt_tokens == 42
    caps = fetch_model_capabilities(settings)
    assert caps.context_window == 8192

    assert len(calls) == 5
    assert {url for url, _, _ in calls} == {
        _GATEWAY + "/v1/chat/completions",
        _GATEWAY + "/v1/embeddings",
        _GATEWAY + "/v1/token-count",
        _GATEWAY + "/v1/models/llm-alias/capabilities",
    }
    assert {model for _, _, model in calls if model} == {
        "llm-alias",
        "vlm-alias",
        "bge-m3",
    }
    for _url, headers, _model in calls:
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer api-auth"
        if expected is None:
            assert "X-AI-Client" not in headers
        else:
            assert headers["X-AI-Client"] == expected
