"""Shared pytest isolation for server-side and clean-database runs.

Production/reference data is created by Alembic migrations.  This module keeps
only external-runtime isolation that is specific to tests.
"""

from __future__ import annotations

from typing import Any

import pytest


class _NoopVLMProvider:
    """Offline VLM stand-in used only when upload-identification tests use defaults."""

    def transcribe_image(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        log_context: dict[str, Any] | None = None,
    ) -> str:
        del image_bytes, mime_type, log_context
        return ""


def _test_filename(request: pytest.FixtureRequest) -> str:
    path = getattr(request.node, "path", None)
    return getattr(path, "name", "")


@pytest.fixture(autouse=True)
def _isolate_clean_server_test_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    """Prevent upload-identification tests from calling the external VLM.

    Canonical reference codes are intentionally not seeded here.  Clean/server
    pytest runs ``alembic upgrade head`` first, so tests exercise the same
    default-code catalog that production deployments receive.
    """

    if _test_filename(request) == "test_upload_identification.py":
        monkeypatch.setattr(
            "app.modules.upload_identification.service.OpenAICompatibleVLMProvider",
            lambda _settings: _NoopVLMProvider(),
        )

    yield
