"""v20 project date backfill from root evidence quotes."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _project(**kwargs):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SourceRef,
        SummaryCandidate,
    )

    quote = kwargs.pop("quote", None)
    relation_quote = kwargs.pop("relation_quote", None)
    source_refs = kwargs.pop("source_refs", None)
    if source_refs is None and quote is not None:
        source_refs = [
            SourceRef(document_id=_DOC, page_no=1, quote_text=quote)
        ]
    skills = kwargs.pop("skills", None)
    if skills is None and relation_quote is not None:
        skills = [
            CodeRefCandidate(
                code="TECH-SEC-AD",
                raw_value="AD",
                source_refs=[
                    SourceRef(
                        document_id=_DOC, page_no=1, quote_text=relation_quote
                    )
                ],
            )
        ]
    project = ProjectCandidate(
        source_refs=source_refs or [],
        skills=skills or [],
        **kwargs,
    )
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=[project],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_parse_common_yy_mm_ranges() -> None:
    from app.modules.analysis.compact_v8 import parse_project_date_range_from_texts

    assert parse_project_date_range_from_texts(
        ["2019년 차기 복권통합구축사업\n19.07 ~ \n20.01"],
        project_name="2019년 차기 복권통합구축사업",
    ) == ("2019.07", "2020.01")
    assert parse_project_date_range_from_texts(
        ["국방 ... 사업\n14.02 ~ \n14.12"]
    ) == ("2014.02", "2014.12")
    assert parse_project_date_range_from_texts(
        ["영화진흥위원회 ...\n08.12 ~ \n10.04"]
    ) == ("2008.12", "2010.04")
    assert parse_project_date_range_from_texts(
        ["99.12 ~ 00.02"]
    ) == ("1999.12", "2000.02")


def test_backfill_whitespace_newline_quote() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_project_dates_from_root_evidence,
    )

    doc = _project(
        project_name="2018년 HW사업",
        quote="2018 ... 사업\n18.06 ~ \n19.01",
    )
    out = backfill_project_dates_from_root_evidence(doc)
    project = out.projects[0]
    assert project.start_date == "2018.06"
    assert project.end_date == "2019.01"
    assert project.duration_months == 7


def test_backfill_does_not_overwrite_existing_dates() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_project_dates_from_root_evidence,
    )

    doc = _project(
        project_name="2019년 사업",
        start_date="2019.01",
        end_date="2019.06",
        duration_months=5,
        quote="19.07 ~ 20.01",
    )
    out = backfill_project_dates_from_root_evidence(doc)
    project = out.projects[0]
    assert project.start_date == "2019.01"
    assert project.end_date == "2019.06"
    assert project.duration_months == 5


def test_backfill_fills_only_null_side() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_project_dates_from_root_evidence,
    )

    doc = _project(
        project_name="2019년 사업",
        start_date="2019.07",
        end_date=None,
        quote="19.07 ~ 20.01",
    )
    out = backfill_project_dates_from_root_evidence(doc)
    project = out.projects[0]
    assert project.start_date == "2019.07"
    assert project.end_date == "2020.01"
    assert project.duration_months == 6


def test_invalid_month_or_multiple_ranges_no_fill() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_project_dates_from_root_evidence,
        parse_project_date_range_from_texts,
    )

    assert parse_project_date_range_from_texts(["19.13 ~ 20.01"]) is None
    assert (
        parse_project_date_range_from_texts(
            ["19.07 ~ 20.01", "18.06 ~ 19.01"]
        )
        is None
    )
    doc = _project(project_name="사업", quote="19.07 ~ 20.01\n18.06 ~ 19.01")
    out = backfill_project_dates_from_root_evidence(doc)
    assert out.projects[0].start_date is None
    assert out.projects[0].end_date is None


def test_relation_refs_dates_are_ignored() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_project_dates_from_root_evidence,
    )

    doc = _project(
        project_name="사업",
        quote="사업명만 있는 인용",
        relation_quote="19.07 ~ 20.01",
    )
    out = backfill_project_dates_from_root_evidence(doc)
    assert out.projects[0].start_date is None
    assert out.projects[0].end_date is None


def test_v19_flag_disabled() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19 = resolve_profile_prompt("profile-extract-v19")
    assert v19.backfill_project_dates_from_root_evidence is False
    v20 = resolve_profile_prompt("profile-extract-v20")
    assert v20.backfill_project_dates_from_root_evidence is True
