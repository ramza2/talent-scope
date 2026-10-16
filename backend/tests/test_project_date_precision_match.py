"""PROJECT-only directional month↔day date precision matching."""

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


def test_month_vs_day_same_month_compatible_unit() -> None:
    from app.modules.analysis.diff_engine import _project_date_precision_compatible

    assert _project_date_precision_compatible(
        candidate="2018.06", existing="2018-06-11"
    )
    assert _project_date_precision_compatible(
        candidate="2019.01", existing="2019-01-10"
    )
    assert not _project_date_precision_compatible(
        candidate="2010.04", existing="2010-05-10"
    )
    assert not _project_date_precision_compatible(
        candidate="2018-06-11", existing="2018-06-12"
    )
    # Do not shrink candidate day precision to existing month.
    assert not _project_date_precision_compatible(
        candidate="2018-06-11", existing="2018.06"
    )
    assert _project_date_precision_compatible(
        candidate="2018.06", existing="2018.06"
    )


def test_same_name_customer_month_day_compatible_no_root_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018.06",
                "end_date": "2019.01",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018-06-11",
                "end_date": "2019-01-10",
                "project_summary": "kept",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert not any(
        s.change_type == "REVIEW" and s.candidate_path == "projects[0]"
        for s in project_specs
    )
    assert any(
        s.change_type == "SAME" and s.existing_target_id == UUID(_PROJ_A)
        for s in project_specs
    )
    assert not any(
        s.field_name in {"start_date", "end_date"} and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_limited_name_variant_month_day_identity_fallback() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2016년 정보자원통합구축 HW2사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2016.08",
                "end_date": "2017.03",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2016년 범정부 정보자원 통합구축 HW2사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2016-08-25",
                "end_date": "2017-03-25",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert not any(s.change_type == "NEW" for s in project_specs)
    assert not any(
        s.change_type == "REVIEW" and s.candidate_path == "projects[0]"
        for s in project_specs
    )
    assert any(s.existing_target_id == UUID(_PROJ_A) for s in project_specs)
    assert not any(
        s.field_name in {"start_date", "end_date"} and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_multiple_precision_compatible_existing_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018.06",
                "end_date": "2019.01",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018-06-11",
                "end_date": "2019-01-10",
            },
            {
                "id": _PROJ_B,
                "project_name": "정보자원 통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018-06-01",
                "end_date": "2019-01-31",
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


def test_real_month_mismatch_remains_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "차세대 시스템 구축",
                "customer_name": "기획재정부",
                "start_date": "2008.12",
                "end_date": "2010.04",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "차세대 시스템 구축",
                "customer_name": "기획재정부",
                "start_date": "2008-12-08",
                "end_date": "2010-05-10",
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


def test_exact_day_vs_different_exact_day_incompatible() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2019-07-15",
                "end_date": "2020-06-30",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "복권통합구축사업",
                "customer_name": "동행복권",
                "start_date": "2019-07-16",
                "end_date": "2020-06-30",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT"
        and s.change_type == "REVIEW"
        and s.candidate_path == "projects[0]"
        for s in specs
    )


def test_matched_precision_compatible_duration_null_fill_still_update() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018.06",
                "end_date": "2019.01",
                "duration_months": 7,
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "정보자원통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2018-06-11",
                "end_date": "2019-01-10",
                "duration_months": None,
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert not any(
        s.change_type == "REVIEW" and s.candidate_path == "projects[0]"
        for s in project_specs
    )
    assert not any(
        s.field_name in {"start_date", "end_date"} and s.change_type == "CONFLICT"
        for s in project_specs
    )
    assert any(
        s.field_name == "duration_months"
        and s.change_type == "UPDATE"
        and s.new_value == 7
        for s in project_specs
    )
