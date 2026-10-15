"""PROJECT same-customer + start-compatible + overlapping-period REVIEW."""

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

_PROJ_A = "e4b817d9-75b7-4073-b407-ea1821206157"
_PROJ_B = "22222222-2222-2222-2222-222222222222"
_PROJ_C = "33333333-3333-3333-3333-333333333333"


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


def test_same_customer_ws_same_start_overlap_end_mismatch_targeted_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "정부통합 전산센터",
                "start_date": "2011.06",
                "end_date": "2011.12",
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
    assert not any(
        s.entity_type == "PROJECT" and s.change_type == "SAME" for s in specs
    )


def test_same_customer_different_start_month_stays_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2011.07",
                "end_date": "2011.12",
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
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_same_customer_non_overlap_stays_new() -> None:
    from app.ai.schemas.profile_candidate import ProjectCandidate
    from app.modules.analysis.diff_engine import (
        _project_customer_overlap_review_matches,
        _project_periods_overlap,
        build_diffs,
    )

    assert not _project_periods_overlap(
        cand_start="2011.06",
        cand_end="2011.07",
        row_start="2011-09-01",
        row_end="2011-11-15",
    )
    # Same start month is required for the fallback; disjoint later period with
    # a different start month cannot enter overlap REVIEW → NEW.
    assert (
        _project_customer_overlap_review_matches(
            ProjectCandidate(
                project_name="2011년 후속 사업",
                customer_name="정부통합전산센터",
                start_date="2011.09",
                end_date="2011.12",
            ),
            [
                {
                    "id": _PROJ_A,
                    "project_name": "2011년 HW자원통합구축 사업2-1(대전)",
                    "customer_name": "정부통합전산센터",
                    "start_date": "2011-06-10",
                    "end_date": "2011-07-15",
                }
            ],
        )
        == []
    )
    candidate = _doc(
        [
            {
                "project_name": "2011년 후속 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2011.09",
                "end_date": "2011.12",
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
                "end_date": "2011-07-15",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_different_customer_stays_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "국방부",
                "start_date": "2011.06",
                "end_date": "2011.12",
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
        s.entity_type == "PROJECT" and s.change_type == "NEW" for s in specs
    )


def test_multiple_overlapping_candidates_targetless_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "정부통합전산센터",
                "start_date": "2011.06",
                "end_date": "2011.12",
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
            },
            {
                "id": _PROJ_B,
                "project_name": "2011년 별도 HW 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2011-06-01",
                "end_date": "2011-10-31",
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


def test_customer_date_precision_compatible_still_takes_precedence() -> None:
    """Existing same-customer/date REVIEW (both ends compatible) unchanged."""
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
                "id": _PROJ_C,
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
    assert reviews[0].existing_target_id == UUID(_PROJ_C)


def test_fresh_replay_project6_review_project8_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "placeholder0",
                "customer_name": "정부통합전산센터",
                "start_date": "2018.01",
                "end_date": "2018.06",
            },
            {
                "project_name": "placeholder1",
                "customer_name": "정부통합전산센터",
                "start_date": "2017.01",
                "end_date": "2017.06",
            },
            {
                "project_name": "placeholder2",
                "customer_name": "정부통합전산센터",
                "start_date": "2016.01",
                "end_date": "2016.06",
            },
            {
                "project_name": "placeholder3",
                "customer_name": "정부통합전산센터",
                "start_date": "2015.01",
                "end_date": "2015.06",
            },
            {
                "project_name": "placeholder4",
                "customer_name": "정부통합전산센터",
                "start_date": "2014.01",
                "end_date": "2014.06",
            },
            {
                "project_name": "placeholder5",
                "customer_name": "정부통합전산센터",
                "start_date": "2013.01",
                "end_date": "2013.06",
            },
            {
                "project_name": "2011년 제2차 자원통합 구축 HW1 사업(대전)",
                "customer_name": "정부통합 전산센터",
                "start_date": "2011.06",
                "end_date": "2011.12",
            },
            {
                "project_name": "placeholder7",
                "customer_name": "정부통합전산센터",
                "start_date": "2010.01",
                "end_date": "2010.06",
            },
            {
                "project_name": "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2008.03",
                "end_date": "2008.09",
            },
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
    p6 = [
        s
        for s in specs
        if s.entity_type == "PROJECT"
        and s.candidate_path == "projects[6]"
        and s.field_name is None
    ]
    assert len(p6) == 1
    assert p6[0].change_type == "REVIEW"
    assert p6[0].existing_target_id == UUID(_PROJ_A)
    p8 = [
        s
        for s in specs
        if s.entity_type == "PROJECT"
        and s.candidate_path == "projects[8]"
        and s.field_name is None
    ]
    assert len(p8) == 1
    assert p8[0].change_type == "NEW"
