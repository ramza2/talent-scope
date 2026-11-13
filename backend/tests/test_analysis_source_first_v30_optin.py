"""Single-run v30 opt-in contract: manual API only; auto/default remain v25."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai.prompts.profile_extract import SOURCE_FIRST_V30_PROMPT_VERSION
from app.modules.analysis.schemas import CreateAnalysisRequest
from app.modules.analysis.service import AnalysisService


def test_single_run_flag_is_explicit_strict_boolean():
    base = {"person_id": uuid4(), "document_ids": [uuid4()]}

    assert CreateAnalysisRequest(**base).source_first_v30 is False
    assert CreateAnalysisRequest(**base, source_first_v30=True).source_first_v30 is True

    for invalid in ("true", "yes", 1, None):
        with pytest.raises(ValidationError):
            CreateAnalysisRequest(**base, source_first_v30=invalid)


def test_explicit_v30_manual_run_does_not_change_next_default_run(monkeypatch):
    person_id, document_id, actor_id = uuid4(), uuid4(), uuid4()
    written, audits, enqueued = [], [], []

    class FakeRepo:
        def get_person(self, value, *, for_update):
            assert value == person_id and for_update
            return SimpleNamespace(status="ACTIVE", deleted_at=None)

        def get_manual_create_blocking_run(self, value):
            assert value == person_id
            return None

        def get_profile(self, value, *, for_update):
            assert value == person_id and for_update
            return SimpleNamespace(profile_version=9)

        def list_documents_by_ids(self, ids):
            assert ids == [document_id]
            return [(
                SimpleNamespace(processing_status="READY", original_filename="resume.pdf"),
                SimpleNamespace(person_id=person_id),
                "이력서",
            )]

        def create_run(self, **kwargs):
            written.append(kwargs)
            return SimpleNamespace(id=uuid4(), status="QUEUED")

        def add_run_documents(self, run_id, ids):
            assert ids == [document_id]

        def add_audit(self, **kwargs):
            audits.append(kwargs)

    service = AnalysisService.__new__(AnalysisService)
    service.repo = FakeRepo()
    service.db = SimpleNamespace(commit=lambda: None)
    service.settings = SimpleNamespace(
        analysis_source_first_v30_enabled=False,
        llm_model="test-llm",
        vlm_model="test-vlm",
    )

    monkeypatch.setattr(
        "app.tasks.analysis_tasks.enqueue_profile_analysis",
        lambda run_id, user_id: enqueued.append((run_id, user_id)),
    )

    for selected in (True, False):
        request = CreateAnalysisRequest(
            person_id=person_id,
            document_ids=[document_id],
            source_first_v30=selected,
        )
        response = service.create_analysis(request, actor_id)
        assert response.status == "QUEUED"

    assert [r["prompt_version"] for r in written] == [
        SOURCE_FIRST_V30_PROMPT_VERSION,
        "profile-extract-v25",
    ]
    assert len(enqueued) == 2
    assert all(user_id == actor_id for _, user_id in enqueued)
    assert [a["after"]["source_first_v30_opt_in"] for a in audits] == [
        True,
        False,
    ]
    assert [a["after"]["prompt_version"] for a in audits] == [
        SOURCE_FIRST_V30_PROMPT_VERSION,
        "profile-extract-v25",
    ]
    assert service._selected_profile_prompt().prompt_version == "profile-extract-v25"


def test_global_off_keeps_automatic_run_selection_v25():
    service = AnalysisService.__new__(AnalysisService)
    service.settings = SimpleNamespace(analysis_source_first_v30_enabled=False)
    assert service._selected_profile_prompt().prompt_version == "profile-extract-v25"
    assert (
        service._selected_profile_prompt(source_first_v30=True).prompt_version
        == SOURCE_FIRST_V30_PROMPT_VERSION
    )
