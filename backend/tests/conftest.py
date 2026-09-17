"""Shared pytest isolation for server-side and clean-database runs.

Keep production validation strict while making tests independent from developer DB
seed state and external AI endpoints.
"""

from __future__ import annotations

from typing import Any

import pytest


_ANALYSIS_REFERENCE_CODES = (
    ("JOB-AI-DEV", "JOB", "AI 개발자"),
    ("TECH-LANG-PYTHON", "TECH", "Python"),
    ("EXP-AI-RAG", "EXP", "RAG"),
    ("BIZ-PUBLIC", "BIZ", "공공"),
)


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
    """Supply only the shared state that clean/server pytest cannot assume.

    - Analysis confirm tests use a few canonical reference codes directly. Seed
      those codes for that module instead of weakening production FK/code checks.
    - Upload-identification tests must never fall through to the configured
      external VLM when they intentionally exercise local parser/converter/LLM
      behavior. Replace only the service's default VLM constructor; explicitly
      injected VLM fakes remain untouched.
    """

    filename = _test_filename(request)

    if filename == "test_upload_identification.py":
        monkeypatch.setattr(
            "app.modules.upload_identification.service.OpenAICompatibleVLMProvider",
            lambda _settings: _NoopVLMProvider(),
        )

    if filename == "test_analysis.py" and "db_session" in request.fixturenames:
        from app.db.models.code import CodeMaster

        db = request.getfixturevalue("db_session")
        changed = False
        for code, code_type, name in _ANALYSIS_REFERENCE_CODES:
            row = db.get(CodeMaster, code)
            if row is None:
                db.add(
                    CodeMaster(
                        code=code,
                        code_type=code_type,
                        name=name,
                        sort_order=1,
                        is_active=True,
                    )
                )
                changed = True
                continue

            # A reused *_test DB must be deterministic as well.
            if row.code_type != code_type or row.name != name or not row.is_active:
                row.code_type = code_type
                row.name = name
                row.sort_order = 1
                row.is_active = True
                db.add(row)
                changed = True

        if changed:
            db.commit()

    yield
