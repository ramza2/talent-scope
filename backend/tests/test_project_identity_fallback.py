"""Project diff: date-anchored identity fallback for limited name variants."""

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


def test_limited_name_variant_same_dates_customer_links_not_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert not any(s.change_type == "NEW" for s in project_specs)
    assert any(
        str(s.existing_target_id) == _PROJ_A
        for s in project_specs
        if s.existing_target_id is not None
    )
    # Name spelling differs → field CONFLICT (or root with target); not unbound NEW.
    assert any(
        s.existing_target_id == UUID(_PROJ_A)
        and (
            s.change_type in {"SAME", "CONFLICT", "UPDATE"}
            or (s.change_type == "CONFLICT" and s.field_name == "project_name")
        )
        for s in project_specs
    )


def test_identity_fallback_preserves_date_normalization() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017.10.19",
                "end_date": "2018.03.22",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
                "project_summary": "kept",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert not any(s.change_type == "NEW" for s in project_specs)
    assert not any(
        s.field_name in {"start_date", "end_date"} and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_unrelated_project_with_new_dates_stays_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 정보자원 통합구축 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2011-11-16",
                "end_date": "2011-12-24",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT"
        and s.change_type == "NEW"
        and isinstance(s.new_value, dict)
        and s.new_value.get("start_date") == "2011-11-16"
        for s in specs
    )


def test_multiple_identity_fallback_candidates_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            },
            {
                "id": _PROJ_B,
                "project_name": "2017년 제2차 정보자원 통합구축 HW1사업(대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
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


def test_name_alone_without_date_anchor_does_not_identity_match() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2019-01-01",
                "end_date": "2019-12-31",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_customer_whitespace_only_links_same_without_conflict() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 HW자원통합구축 사업2-1(대전)",
                "customer_name": "정부통합 전산센터",
                "start_date": "2011-06-10",
                "end_date": "2011-11-15",
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
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert any(
        s.change_type == "SAME" and s.existing_target_id == UUID(_PROJ_A)
        for s in project_specs
    )
    assert not any(s.change_type == "NEW" for s in project_specs)
    assert not any(
        s.field_name == "customer_name" and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_different_customer_same_name_dates_unique_review_with_target() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013-09-16",
                "end_date": "2013-12-31",
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


def test_different_customer_same_name_dates_multiple_review_without_target() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013-09-16",
                "end_date": "2013-12-31",
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
                "start_date": "2013-09-16",
                "end_date": "2013-12-31",
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


def test_true_new_2011_nov_dec_dates_still_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 HW자원통합구축 추가사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2011-11-16",
                "end_date": "2011-12-24",
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
        and s.new_value.get("start_date") == "2011-11-16"
        for s in specs
    )
