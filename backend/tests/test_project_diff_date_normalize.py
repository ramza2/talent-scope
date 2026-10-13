"""Project diff: date format equivalence and unique-similar existing_target_id."""

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


def _candidate_project(
    *,
    project_name: str = "복권통합구축사업",
    customer_name: str = "동행복권",
    start_date: str,
    end_date: str,
    project_summary: str = "summary",
):
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
        projects=[
            ProjectCandidate(
                project_name=project_name,
                customer_name=customer_name,
                start_date=start_date,
                end_date=end_date,
                project_summary=project_summary,
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_same_project_dash_vs_dot_dates_no_false_root_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate_project(
        start_date="2019.07.15",
        end_date="2020.06.30",
        project_summary="same",
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2019-07-15",
                "end_date": "2020-06-30",
                "project_summary": "same",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert project_specs
    assert all(s.change_type != "REVIEW" for s in project_specs)
    assert any(s.change_type == "SAME" for s in project_specs)
    assert not any(
        s.field_name in {"start_date", "end_date"} and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_unique_similar_review_sets_existing_target_id() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate_project(
        start_date="2022-01-01",
        end_date="2022-12-31",
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2019-07-15",
                "end_date": "2020-06-30",
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


def test_multiple_similar_review_leaves_existing_target_id_unset() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate_project(
        start_date="2022-01-01",
        end_date="2022-12-31",
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2019-07-15",
                "end_date": "2020-06-30",
            },
            {
                "id": _PROJ_B,
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2018-01-01",
                "end_date": "2018-12-31",
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
