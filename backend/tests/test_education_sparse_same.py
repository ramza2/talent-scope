"""EDUCATION unique-similar: sparse candidate without new facts → SAME."""

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

_EDU_A = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_EDU_B = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _edu_candidate(**kwargs):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        EducationCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        education=[EducationCandidate(**kwargs)],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_sparse_candidate_dates_omitted_same_school_degree_is_same() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _edu_candidate(school_name="대진대학교", degree="학사")
    base = {
        "profile": {"name": "홍길동"},
        "education": [
            {
                "id": _EDU_A,
                "school_name": "대진대학교",
                "degree": "학사",
                "start_date": "2010-03-01",
                "end_date": "2014-02-28",
                "major": "컴퓨터공학",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    edu = [s for s in specs if s.entity_type == "EDUCATION"]
    assert len(edu) == 1
    assert edu[0].change_type == "SAME"
    assert edu[0].existing_target_id == UUID(_EDU_A)
    assert edu[0].candidate_path == "education[0]"


def test_unique_similar_null_major_filled_is_update() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _edu_candidate(
        school_name="대진대학교",
        degree="학사",
        major="컴퓨터공학",
    )
    base = {
        "profile": {"name": "홍길동"},
        "education": [
            {
                "id": _EDU_A,
                "school_name": "대진대학교",
                "degree": "학사",
                "start_date": "2010-03-01",
                "end_date": "2014-02-28",
                "major": None,
            }
        ],
    }
    specs = build_diffs(candidate, base)
    edu = [s for s in specs if s.entity_type == "EDUCATION"]
    assert any(
        s.change_type == "UPDATE"
        and s.field_name == "major"
        and s.existing_target_id == UUID(_EDU_A)
        and s.new_value == "컴퓨터공학"
        for s in edu
    )
    assert not any(s.change_type == "REVIEW" for s in edu)


def test_unique_similar_major_conflict_stays_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _edu_candidate(
        school_name="대진대학교",
        degree="학사",
        major="전자공학",
    )
    base = {
        "profile": {"name": "홍길동"},
        "education": [
            {
                "id": _EDU_A,
                "school_name": "대진대학교",
                "degree": "학사",
                "start_date": "2010-03-01",
                "end_date": "2014-02-28",
                "major": "컴퓨터공학",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    edu = [s for s in specs if s.entity_type == "EDUCATION"]
    assert any(
        s.change_type == "REVIEW"
        and s.existing_target_id == UUID(_EDU_A)
        and s.candidate_path == "education[0]"
        for s in edu
    )


def test_multiple_similar_education_stays_review() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _edu_candidate(school_name="대진대학교", degree="학사")
    base = {
        "profile": {"name": "홍길동"},
        "education": [
            {
                "id": _EDU_A,
                "school_name": "대진대학교",
                "degree": "학사",
                "start_date": "2010-03-01",
                "end_date": "2014-02-28",
            },
            {
                "id": _EDU_B,
                "school_name": "대진대학교",
                "degree": "학사",
                "start_date": "2015-03-01",
                "end_date": "2017-02-28",
            },
        ],
    }
    specs = build_diffs(candidate, base)
    edu = [s for s in specs if s.entity_type == "EDUCATION"]
    assert any(
        s.change_type == "REVIEW"
        and s.existing_target_id is None
        and s.candidate_path == "education[0]"
        for s in edu
    )
    assert not any(s.change_type == "SAME" for s in edu)
