"""career_document_value semantic-equivalent cleanup → UPDATE diff."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"


def _candidate(*, career_document_value: str):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(
            name="홍길동",
            career_document_value=career_document_value,
        ),
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _cdv_spec(specs):
    return next(
        s
        for s in specs
        if s.entity_type == "PROFILE" and s.field_name == "career_document_value"
    )


def _ccm_spec(specs):
    return next(
        (
            s
            for s in specs
            if s.entity_type == "PROFILE" and s.field_name == "career_confirmed_months"
        ),
        None,
    )


def test_same_months_canonical_new_is_update() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(
        career_document_value="13년 8개월",
    )
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": (
                "13년 8개월 시스템구축 및 운영, 정보자원 통합구축 프로젝트 관리"
            ),
            "career_confirmed_months": 164,
        }
    }
    specs = build_diffs(candidate, base)
    cdv = _cdv_spec(specs)
    assert cdv.change_type == "UPDATE"
    assert cdv.old_value.startswith("13년 8개월")
    assert cdv.new_value == "13년 8개월"
    ccm = _ccm_spec(specs)
    assert ccm is not None
    assert ccm.change_type == "SAME"
    assert ccm.new_value == 164


def test_prefix_noise_canonical_new_is_update() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="16년")
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": "기술경력 16년",
        }
    }
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "UPDATE"


def test_exact_same_is_same() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="13년 8개월")
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": "13년 8개월",
            "career_confirmed_months": 164,
        }
    }
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "SAME"
    assert _ccm_spec(specs).change_type == "SAME"


def test_different_months_is_conflict() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="13년 8개월")
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": "13년 7개월",
        }
    }
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "CONFLICT"


def test_unparseable_old_is_conflict() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="13년")
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": "약 13년",
        }
    }
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "CONFLICT"


def test_noncanonical_new_is_conflict() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="13년 8개월, 특급기술자")
    base = {
        "profile": {
            "name": "홍길동",
            "career_document_value": "13년 8개월",
        }
    }
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "CONFLICT"


def test_null_old_is_new() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _candidate(career_document_value="13년 8개월")
    base = {"profile": {"name": "홍길동", "career_document_value": None}}
    specs = build_diffs(candidate, base)
    assert _cdv_spec(specs).change_type == "NEW"
