"""Employment ongoing/invalid end_date: unit-level diff + date helpers."""

from __future__ import annotations

import pytest


def test_ongoing_end_marker_normalize_and_detect():
    from app.modules.analysis.confirm_dates import (
        is_ongoing_end_marker,
        normalize_confirmed_date,
    )

    assert is_ongoing_end_marker("현재")
    assert is_ongoing_end_marker("재직중")
    assert is_ongoing_end_marker("Present")
    assert is_ongoing_end_marker("PRESENT")
    assert not is_ongoing_end_marker("2020-01")
    assert not is_ongoing_end_marker("모름")

    assert (
        normalize_confirmed_date(
            "현재", bound="end", treat_ongoing_end_as_null=True
        )
        is None
    )
    assert (
        normalize_confirmed_date(
            "Present", bound="end", treat_ongoing_end_as_null=True
        )
        is None
    )
    with pytest.raises(ValueError, match="invalid date"):
        normalize_confirmed_date("현재", bound="end", treat_ongoing_end_as_null=False)
    # Start bound must not swallow ongoing tokens.
    with pytest.raises(ValueError, match="invalid date"):
        normalize_confirmed_date(
            "현재", bound="start", treat_ongoing_end_as_null=True
        )


def test_employment_ongoing_end_can_be_new_invalid_becomes_review():
    from app.ai.schemas.profile_candidate import (
        EmploymentCandidate,
        ProfileCandidateDocument,
    )
    from app.modules.analysis.diff_engine import build_diffs

    base = {
        "profile": {},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [],
        "certifications": [],
        "projects": [],
    }
    candidate = ProfileCandidateDocument(
        employment_history=[
            EmploymentCandidate(
                company_name="Alpha",
                start_date="2020.07",
                end_date="현재",
                title="Dev",
            ),
            EmploymentCandidate(
                company_name="Beta",
                start_date="2018",
                end_date="모름",
                title="PL",
            ),
        ]
    )
    specs = build_diffs(candidate, base)
    emp = [s for s in specs if s.entity_type == "EMPLOYMENT"]

    alpha = next(
        s
        for s in emp
        if isinstance(s.new_value, dict) and s.new_value.get("company_name") == "Alpha"
    )
    # Ongoing end is confirm-safe → NEW; original AI value preserved in new_value.
    assert alpha.change_type == "NEW"
    assert alpha.new_value.get("end_date") == "현재"

    beta = next(
        s
        for s in emp
        if isinstance(s.new_value, dict) and s.new_value.get("company_name") == "Beta"
    )
    assert beta.change_type == "REVIEW"
    assert beta.new_value.get("end_date") == "모름"
