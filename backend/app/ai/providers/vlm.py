"""VLM provider protocol and OpenAI-compatible implementation."""

from __future__ import annotations

import base64
from typing import Protocol

from app.ai.prompts.vlm_transcribe_v1 import (
    VLM_TRANSCRIBE_SYSTEM,
    VLM_TRANSCRIBE_USER,
)
from app.ai.providers.errors import AIProviderError
from app.ai.providers.openai_compat import (
    extract_message_content,
    post_chat_completions,
)
from app.core.config import Settings, get_settings


class VLMProvider(Protocol):
    def transcribe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        log_context: dict | None = None,
    ) -> str: ...


class OpenAICompatibleVLMProvider:
    """Vision chat completions used only for visible-text transcription."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def transcribe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        log_context: dict | None = None,
    ) -> str:
        if not image_bytes:
            raise AIProviderError("empty image for VLM")
        timeout = float(self.settings.ai_request_timeout_seconds)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{b64}"
        payload = {
            "model": self.settings.vlm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": VLM_TRANSCRIBE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VLM_TRANSCRIBE_USER},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        }
        ctx = {
            **(log_context or {}),
            "provider": "vlm",
            "image_bytes": len(image_bytes),
        }
        data = post_chat_completions(
            base_url=self.settings.vlm_base_url,
            api_key=self.settings.vlm_api_key,
            payload=payload,
            timeout_seconds=timeout,
            log_context=ctx,
        )
        return extract_message_content(data).strip()


class FakeVLMProvider:
    """Deterministic VLM for tests."""

    def __init__(self, text: str = "홍길동\nABC테크\n010-1234-5678", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    def transcribe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str = "image/png",
        log_context: dict | None = None,
    ) -> str:
        self.calls += 1
        _ = (image_bytes, mime_type, log_context)
        if self.fail:
            raise AIProviderError("injected VLM failure")
        return self.text
