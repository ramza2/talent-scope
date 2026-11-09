"""Keep project ongoing date preview and confirm apply behavior aligned."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.analysis.confirm import (
    _ConfirmContext,
    ConfirmValidationError,
    collect_confirm_date_issues,
)


@pytest.mark.parametrize("end_marker", ["현재", "진행 중", "Present"])
def test_project_ongoing_end_date_preview_matches_confirm_apply(
    end_marker: str,
) -> None:
    value = {
        "project_name": "진행 중인 사업",
        "start_date": "2023.07",
        "end_date": end_marker,
    }
    diff = SimpleNamespace(
        entity_type="PROJECT",
        candidate_path="projects[15]",
        field_name=None,
        review_status="ACCEPTED",
        new_value=value,
        decided_value=None,
    )

    # The UI preview reports no date issues; confirm must accept the same value.
    assert collect_confirm_date_issues([diff]) == []

    context = object.__new__(_ConfirmContext)
    payload = context._project_scalar_payload(value)
    assert "end_date" in payload
    assert payload["end_date"] is None
    # Project child-field confirmation uses the same scalar conversion.
    assert context._coerce_project_scalar("end_date", end_marker) is None


def test_project_start_date_still_rejects_ongoing_marker() -> None:
    context = object.__new__(_ConfirmContext)
    with pytest.raises(ConfirmValidationError, match="invalid date"):
        context._coerce_project_scalar("start_date", "현재")
