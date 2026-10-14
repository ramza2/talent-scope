"""PROJECT name_date REVIEW: precision-compatible dates + customer mismatch."""

from __future__ import annotations

import os
from uuid import UUID

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_PROJ_A = "11111111-1111-1111-1111-111111111111"
_PROJ_B = "22222222-2222-2222-2222-222222222222"


def _doc(projects: list[dict]):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=[ProjectCandidate(**p) for p in projects],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_same_name_month_day_different_customer_targeted_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013.09",
                "end_date": "2013.12",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "기획재정부",
                "start_date": "2013-09-16",
                "end_date": "2013-12-31",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    reviews = [
        s
        for s in specs
        if s.entity_type == "PROJECT"
        and s.change_type == "REVIEW"
        and s.candidate_path == "projects[0]"
    ]
    assert len(reviews) == 1
    assert reviews[0].existing_target_id == UUID(_PROJ_A)
    assert not any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_same_name_real_month_mismatch_name_date_does_not_match() -> None:
    from app.modules.analysis.diff_engine import (
        _project_name_date_review_matches,
        build_diffs,
    )
    from app.ai.schemas.profile_candidate import ProjectCandidate

    project = ProjectCandidate(
        project_name="2013년 차기 복권통합구축사업",
        customer_name="복권위원회",
        start_date="2013.12",
        end_date="2014.03",
    )
    existing = [
        {
            "id": _PROJ_A,
            "project_name": "2013년 차기 복권통합구축사업",
            "customer_name": "기획재정부",
            "start_date": "2014-01-01",
            "end_date": "2014-03-31",
        }
    ]
    assert _project_name_date_review_matches(project, existing) == []

    candidate = _doc(
        [
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013.12",
                "end_date": "2014.03",
            }
        ]
    )
    base = {"profile": {"name": "홍길동"}, "projects": existing}
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_different_name_name_date_does_not_match() -> None:
    from app.modules.analysis.diff_engine import _project_name_date_review_matches
    from app.ai.schemas.profile_candidate import ProjectCandidate

    project = ProjectCandidate(
        project_name="차세대 시스템 구축",
        customer_name="복권위원회",
        start_date="2013.09",
        end_date="2013.12",
    )
    existing = [
        {
            "id": _PROJ_A,
            "project_name": "2013년 차기 복권통합구축사업",
            "customer_name": "기획재정부",
            "start_date": "2013-09-16",
            "end_date": "2013-12-31",
        }
    ]
    assert _project_name_date_review_matches(project, existing) == []


def test_multiple_name_date_compatible_existing_ambiguous_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013.09",
                "end_date": "2013.12",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "기획재정부",
                "start_date": "2013-09-16",
                "end_date": "2013-12-31",
            },
            {
                "id": _PROJ_B,
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2013-09-01",
                "end_date": "2013-12-15",
            },
        ],
    }
    specs = build_diffs(candidate, base)
    reviews = [
        s
        for s in specs
        if s.entity_type == "PROJECT"
        and s.change_type == "REVIEW"
        and s.candidate_path == "projects[0]"
    ]
    assert len(reviews) == 1
    assert reviews[0].existing_target_id is None
    assert not any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )
