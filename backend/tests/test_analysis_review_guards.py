"""Focused review decision guard tests."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationAppError
from app.db.models.analysis import AnalysisDiffItem
from app.modules.analysis.service import AnalysisService


class _FakeDb:
    def add(self, _value) -> None:
        return None

    def flush(self) -> None:
        return None


class _FakeRepo:
    def __init__(self, *codes: str) -> None:
        self.codes = set(codes)

    def list_active_codes(self, code_types=None):
        del code_types
        return [SimpleNamespace(code=code) for code in sorted(self.codes)]


def _service(*codes: str) -> AnalysisService:
    service = object.__new__(AnalysisService)
    service.db = _FakeDb()
    service.repo = _FakeRepo(*codes)
    return service


def _diff(*, status: str = "PENDING", code: str | None = None) -> AnalysisDiffItem:
    value = {"raw_value": "Java"}
    if code is not None:
        value["code"] = code
    return AnalysisDiffItem(
        analysis_run_id=uuid4(),
        entity_type="TECH",
        candidate_path="skills[0]",
        field_name=None,
        change_type="REVIEW",
        new_value=value,
        review_status=status,
    )


def test_unmapped_tech_review_cannot_be_accepted() -> None:
    from app.modules.analysis.schemas import DiffDecisionRequest

    service = _service("TECH-LANG-PYTHON")
    diff = _diff()

    with pytest.raises(ValidationAppError, match="TECH 코드가 매핑되지 않은"):
        service._apply_decision(
            SimpleNamespace(person_id=uuid4()),
            diff,
            DiffDecisionRequest(review_status="ACCEPTED"),
            uuid4(),
        )


def test_modified_tech_review_requires_active_code() -> None:
    from app.modules.analysis.schemas import DiffDecisionRequest

    service = _service("TECH-LANG-PYTHON")
    diff = _diff()

    with pytest.raises(ValidationAppError, match="유효한 TECH 코드"):
        service._apply_decision(
            SimpleNamespace(person_id=uuid4()),
            diff,
            DiffDecisionRequest(
                review_status="MODIFIED",
                decided_value={"raw_value": "Java", "code": "TECH-LANG-JAVA"},
            ),
            uuid4(),
        )


def test_review_decision_can_be_reset_to_pending() -> None:
    from app.modules.analysis.schemas import DiffDecisionRequest

    service = _service()
    diff = _diff(status="ACCEPTED")
    diff.decided_value = {"raw_value": "Java"}
    diff.decided_by = uuid4()

    service._apply_decision(
        SimpleNamespace(person_id=uuid4()),
        diff,
        DiffDecisionRequest(review_status="PENDING"),
        uuid4(),
    )

    assert diff.review_status == "PENDING"
    assert diff.decided_value is None
    assert diff.decided_by is None
    assert diff.decided_at is None
