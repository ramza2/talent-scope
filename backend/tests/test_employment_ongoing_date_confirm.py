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


def test_employment_start_after_end_becomes_review():
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
                company_name="OrderCo",
                start_date="2022.01",
                end_date="2020.12",
                title="Dev",
            )
        ]
    )
    specs = build_diffs(candidate, base)
    emp = [s for s in specs if s.entity_type == "EMPLOYMENT"]
    assert len(emp) == 1
    assert emp[0].change_type == "REVIEW"
    assert emp[0].new_value.get("start_date") == "2022.01"
    assert emp[0].new_value.get("end_date") == "2020.12"


def test_dates_compatible_ongoing_marker_not_global():
    """EDUCATION/CERT must not treat '현재' as a universal compatible end."""
    from app.modules.analysis.diff_engine import (
        _employment_end_dates_compatible,
        dates_compatible,
    )

    # Generic helper: unparseable vs concrete date → incompatible (not ongoing).
    assert not dates_compatible("현재", "2014-02-28")
    assert not dates_compatible("Present", "2015-05-01")
    # Employment end helper still treats ongoing as omitted-compatible.
    assert _employment_end_dates_compatible("현재", None)
    assert _employment_end_dates_compatible("현재", "2014-02-28")
    assert _employment_end_dates_compatible("Present", "2015-05-01")


def test_education_cert_date_similarity_unchanged_by_ongoing_split():
    from app.ai.schemas.profile_candidate import (
        CertificationCandidate,
        EducationCandidate,
        ProfileCandidateDocument,
    )
    from app.modules.analysis.diff_engine import build_diffs

    base = {
        "profile": {},
        "jobs": [],
        "skills": [],
        "expertise": [],
        "employment_history": [],
        "education": [
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "school_name": "K대",
                "degree": "학사",
                "start_date": "2010-03-01",
                "end_date": "2014-02-28",
            }
        ],
        "certifications": [
            {
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "certification_name": "정보처리기사",
                "issuer": "한국산업인력공단",
                "acquired_date": "2015-05-01",
            }
        ],
        "projects": [],
    }
    # Candidate uses '현재' on education end — must NOT silently match via ongoing.
    candidate = ProfileCandidateDocument(
        education=[
            EducationCandidate(
                school_name="K대",
                degree="학사",
                start_date="2010",
                end_date="현재",
            )
        ],
        certifications=[
            CertificationCandidate(
                certification_name="정보처리기사",
                issuer="한국산업인력공단",
                acquired_date="2015",
            )
        ],
    )
    specs = build_diffs(candidate, base)
    edu = [s for s in specs if s.entity_type == "EDUCATION"]
    # end '현재' must NOT match existing 2014 via employment ongoing rules → NEW.
    assert any(
        s.change_type == "NEW"
        and isinstance(s.new_value, dict)
        and s.new_value.get("school_name") == "K대"
        and s.new_value.get("end_date") == "현재"
        for s in edu
    )

    cert = [s for s in specs if s.entity_type == "CERTIFICATION"]
    # Acquired-date precision match still yields unique similar → REVIEW.
    assert any(
        s.change_type == "REVIEW"
        and isinstance(s.new_value, dict)
        and s.new_value.get("certification_name") == "정보처리기사"
        for s in cert
    )
