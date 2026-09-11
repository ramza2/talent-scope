"""OpenAI-compatible BGE-M3 embedding provider."""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.openai_compat import post_embeddings
from app.core.config import get_settings
from app.db.models.search import EMBEDDING_DIMENSIONS
from app.modules.search.embedding_policy import assert_embedding_dimensions_configured


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed_text(self, text: str) -> list[float]: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def _validate_vector(vector: object, *, expected_dim: int) -> list[float]:
    if not isinstance(vector, list) or not vector:
        raise AIResponseValidationError("embedding response invalid")
    if len(vector) != expected_dim:
        raise AIResponseValidationError("embedding dimension mismatch")
    out: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AIResponseValidationError("embedding response invalid")
        number = float(value)
        if not math.isfinite(number):
            raise AIResponseValidationError("embedding response invalid")
        out.append(number)
    return out


def _parse_embeddings_response(
    data: dict,
    *,
    expected_count: int,
    expected_dim: int,
) -> list[list[float]]:
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise AIResponseValidationError("embedding response invalid")

    by_index: dict[int, list[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise AIResponseValidationError("embedding response invalid")
        index = row.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise AIResponseValidationError("embedding response invalid")
        if index in by_index:
            raise AIResponseValidationError("embedding response invalid")
        by_index[index] = _validate_vector(row.get("embedding"), expected_dim=expected_dim)

    if set(by_index) != set(range(expected_count)):
        raise AIResponseValidationError("embedding response invalid")
    return [by_index[i] for i in range(expected_count)]


class OpenAICompatibleEmbeddingProvider:
    """BGE-M3 via OpenAI-compatible ``POST /v1/embeddings``."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        expected = assert_embedding_dimensions_configured()
        dim = int(dimensions if dimensions is not None else settings.embedding_dimensions)
        if dim != expected:
            raise AIProviderError("embedding dimension mismatch")
        self.base_url = base_url if base_url is not None else settings.embedding_base_url
        self.api_key = api_key if api_key is not None else settings.embedding_api_key
        self.model = model if model is not None else settings.embedding_model
        self.dimensions = dim
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.embedding_request_timeout_seconds
        )

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise AIProviderError("embedding input is blank")
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise AIProviderError("embedding input is blank")

        payload = {"model": self.model, "input": list(texts)}
        data = post_embeddings(
            base_url=self.base_url,
            api_key=self.api_key,
            payload=payload,
            timeout_seconds=self.timeout_seconds,
            log_context={"provider": "openai_compat_embedding", "batch_size": len(texts)},
        )
        return _parse_embeddings_response(
            data,
            expected_count=len(texts),
            expected_dim=self.dimensions,
        )


def get_embedding_provider() -> EmbeddingProvider:
    """Factory used by workers — patchable in tests."""
    return OpenAICompatibleEmbeddingProvider()


EXPECTED_EMBEDDING_DIMENSIONS = EMBEDDING_DIMENSIONS
