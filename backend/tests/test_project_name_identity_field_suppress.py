"""Matched PROJECT: suppress project_name CONFLICT for identity-compatible names."""

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


def test_hw1_space_variant_no_project_name_conflict() -> None:
    from app.modules.analysis.diff_engine import build_diffs

    candidate = _doc(
        [
            {
                "project_name": "2019년 제1차 범정부 정보자원 통합구축 HW1 사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2019-01-01",
                "end_date": "2019-06-30",
            }
        ]
    )
    base = {
        "profile": {"name": "홍길동"},
        "projects": [
            {
                "id": _PROJ_A,
                "project_name": "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
                "customer_name": "정부통합전산센터",
                "start_date": "2019-01-01",
                "end_date": "2019-06-30",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    project_specs = [s for s in specs if s.entity_type == "PROJECT"]
    assert any(
        s.change_type == "SAME" and s.existing_target_id == UUID(_PROJ_A)
        for s in project_specs
    )
    assert not any(
        s.field_name == "project_name" and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_optional_beomjeongbu_and_whitespace_no_project_name_conflict() -> None:
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
    assert any(s.existing_target_id == UUID(_PROJ_A) for s in project_specs)
    assert not any(
        s.field_name == "project_name" and s.change_type == "CONFLICT"
        for s in project_specs
    )


def test_materially_different_project_name_not_suppressed() -> None:
    from app.modules.analysis.diff_engine import (
        _project_names_identity_compatible,
        build_diffs,
    )

    # Suppression reuses identity helper only; non-compatible names stay out.
    assert not _project_names_identity_compatible(
        "차세대 시스템 구축",
        "복권통합구축사업",
    )
    assert not _project_names_identity_compatible(
        "정보자원",
        "2017년 제2차 범정부 정보자원 통합구축 HW1사업 (대전)",
    )

    candidate = _doc(
        [
            {
                "project_name": "차세대 시스템 구축",
                "customer_name": "기획재정부",
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
                "customer_name": "기획재정부",
                "start_date": "2017-10-19",
                "end_date": "2018-03-22",
            }
        ],
    }
    specs = build_diffs(candidate, base)
    assert any(
        s.entity_type == "PROJECT" and s.change_type in {"NEW", "REVIEW"}
        for s in specs
    )
    assert not any(
        s.entity_type == "PROJECT"
        and s.change_type == "SAME"
        and s.existing_target_id == UUID(_PROJ_A)
        for s in specs
    )
