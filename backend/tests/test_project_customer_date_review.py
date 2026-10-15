"""PROJECT same-customer/date potential-match REVIEW (no auto-link)."""

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


def test_same_customer_precision_dates_different_name_targeted_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "국방 메가센터 정보시스템 이전통합사업(육군)",
                "customer_name": "국방부",
                "start_date": "2014.02",
                "end_date": "2014.12",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "국방통합정보관리소 정보체계 이전/통합 사업",
                "customer_name": "국방부",
                "start_date": "2014-02-07",
                "end_date": "2014-12-06",
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
        s.entity_type == "PROJECT" and s.change_type in {"NEW", "SAME", "UPDATE"}
        for s in specs
    )


def test_multiple_customer_date_matches_ambiguous_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "국방 메가센터 정보시스템 이전통합사업(육군)",
                "customer_name": "국방부",
                "start_date": "2014.02",
                "end_date": "2014.12",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "국방통합정보관리소 정보체계 이전/통합 사업",
                "customer_name": "국방부",
                "start_date": "2014-02-07",
                "end_date": "2014-12-06",
            },
            {
                "id": _PROJ_B,
                "project_name": "육군 메가센터 이전 사업",
                "customer_name": "국방부",
                "start_date": "2014-02-01",
                "end_date": "2014-12-31",
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


def test_customer_mismatch_no_customer_date_match() -> None:
    from app.modules.analysis.diff_engine import (
        _project_customer_date_review_matches,
        build_diffs,
    )
    from app.ai.schemas.profile_candidate import ProjectCandidate

    project = ProjectCandidate(
        project_name="국방 메가센터 정보시스템 이전통합사업(육군)",
        customer_name="국방부",
        start_date="2014.02",
        end_date="2014.12",
    )
    existing = [
        {
            "id": _PROJ_A,
            "project_name": "국방통합정보관리소 정보체계 이전/통합 사업",
            "customer_name": "육군",
            "start_date": "2014-02-07",
            "end_date": "2014-12-06",
        }
    ]
    assert _project_customer_date_review_matches(project, existing) == []

    candidate = _doc(
        [
            {
                "project_name": "국방 메가센터 정보시스템 이전통합사업(육군)",
                "customer_name": "국방부",
                "start_date": "2014.02",
                "end_date": "2014.12",
            }
        ]
    )
    specs = build_diffs(candidate, {"profile": {"name": "홍길동"}, "projects": existing})
    assert any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_end_month_mismatch_no_customer_date_match() -> None:
    """Precision end mismatch still misses customer-date REVIEW helper.

    Overlap REVIEW (separate fallback) may still surface a targeted REVIEW.
    """
    from app.modules.analysis.diff_engine import (
        _project_customer_date_review_matches,
        build_diffs,
    )
    from app.ai.schemas.profile_candidate import ProjectCandidate

    project = ProjectCandidate(
        project_name="2011년 HW자원통합구축 추가사업",
        customer_name="정부통합전산센터",
        start_date="2011.06",
        end_date="2012.01",
    )
    existing = [
        {
            "id": _PROJ_A,
            "project_name": "2011년 HW자원통합구축 사업2-1(대전)",
            "customer_name": "정부통합전산센터",
            "start_date": "2011-06-10",
            "end_date": "2011-11-15",
        }
    ]
    assert _project_customer_date_review_matches(project, existing) == []

    candidate = _doc(
        [
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2011.06",
                "end_date": "2012.01",
            }
        ]
    )
    specs = build_diffs(candidate, {"profile": {"name": "홍길동"}, "projects": existing})
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


def test_unrelated_project_with_different_dates_stays_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2008.03",
                "end_date": "2008.09",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2011년 HW자원통합구축 사업2-1(대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2011-06-10",
                "end_date": "2011-11-15",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT"
        and s.change_type == "NEW"
        and isinstance(s.new_value, dict)
        and s.new_value.get("project_name")
        == "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업"
        for s in specs
    )
