"""Focused contract tests for a shared OpenAI-compatible AI gateway."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from app.ai.providers.embedding import OpenAICompatibleEmbeddingProvider
from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.ai.providers.openai_compat import (
    normalize_chat_completions_url,
    normalize_embeddings_url,
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


def test_embedding_gateway_keeps_alias_and_1024_dimensions() -> None:
    captured: dict[str, object] = {}
    vector = [0.01] * 1024

    def fake_post_embeddings(**kwargs):
        captured.update(kwargs)
        return {
            "data": [{"index": 0, "embedding": vector}],
            "model": "embedding-alias",
        }

    with patch(
        "app.ai.providers.embedding.assert_embedding_dimensions_configured",
        return_value=1024,
    ), patch(
        "app.ai.providers.embedding.post_embeddings",
        side_effect=fake_post_embeddings,
    ):
        provider = OpenAICompatibleEmbeddingProvider(
            base_url=_GATEWAY,
            api_key="",
            model="embedding-alias",
            dimensions=1024,
        )
        result = provider.embed_text("gateway compatibility")

    assert len(result) == 1024
    assert captured["base_url"] == _GATEWAY
    assert captured["payload"]["model"] == "embedding-alias"
    assert captured["payload"]["input"] == ["gateway compatibility"]
