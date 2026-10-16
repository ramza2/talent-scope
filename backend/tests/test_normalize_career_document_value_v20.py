"""v20 career_document_value → canonical duration-only text."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _doc(*, career_document_value: str, technical_grade: str = "EXPERT"):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SourceRef,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(
            name="홍길동",
            career_document_value=career_document_value,
            technical_grade=technical_grade,
            source_refs={
                "career_document_value": [
                    SourceRef(
                        document_id=_DOC_ID,
                        page_no=1,
                        quote_text=career_document_value,
                    )
                ],
                "technical_grade": [
                    SourceRef(
                        document_id=_DOC_ID,
                        page_no=1,
                        quote_text="특급기술자",
                    )
                ],
            },
        ),
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_strip_grade_suffix() -> None:
    from app.modules.analysis.compact_v8 import normalize_career_document_value

    doc = _doc(career_document_value="13년 8개월, 특급기술자")
    out = normalize_career_document_value(doc)
    assert out.profile.career_document_value == "13년 8개월"
    assert out.profile.technical_grade == "EXPERT"
    assert out.profile.source_refs["career_document_value"][0].quote_text == (
        "13년 8개월, 특급기술자"
    )
    assert out.profile.source_refs["technical_grade"][0].quote_text == "특급기술자"


def test_strip_prefix_years_only() -> None:
    from app.modules.analysis.career_document_months import (
        normalize_career_document_value_text,
    )

    assert normalize_career_document_value_text("기술경력 16년") == "16년"


def test_strip_prefix_years_and_months() -> None:
    from app.modules.analysis.career_document_months import (
        normalize_career_document_value_text,
    )

    assert (
        normalize_career_document_value_text("SW기술자 경력 12년 4개월")
        == "12년 4개월"
    )


def test_months_only_kept() -> None:
    from app.modules.analysis.career_document_months import (
        normalize_career_document_value_text,
    )

    assert normalize_career_document_value_text("10개월") == "10개월"


def test_approximate_range_unparseable_unchanged() -> None:
    from app.modules.analysis.career_document_months import (
        normalize_career_document_value_text,
    )

    assert normalize_career_document_value_text("약 16년") == "약 16년"
    assert normalize_career_document_value_text("16년 이상") == "16년 이상"
    assert normalize_career_document_value_text("경력 풍부") == "경력 풍부"


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.normalize_career_document_value is False
    assert not hasattr(v19, "NORMALIZE_CAREER_DOCUMENT_VALUE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.NORMALIZE_CAREER_DOCUMENT_VALUE is True
    assert current_profile_prompt().normalize_career_document_value is True
