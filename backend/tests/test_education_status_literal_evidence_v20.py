"""v20 education status requires literal status-word quote evidence."""

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


def _ref(quote: str):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=_DOC_ID, page_no=1, quote_text=quote)


def _edu_doc(*, status: str | None, quote: str, degree: str = "학사"):
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
        education=[
            EducationCandidate(
                school_name="대진대학교",
                major="도시공학과",
                degree=degree,
                status=status,
                start_date=None,
                end_date=None,
                source_refs=[_ref(quote)],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_graduated_with_jolup_quote_kept() -> None:
    from app.modules.analysis.compact_v8 import (
        clear_education_status_without_literal_evidence,
    )

    doc = _edu_doc(status="GRADUATED", quote="대진대학교 졸업")
    out = clear_education_status_without_literal_evidence(doc)
    assert out.education[0].status == "GRADUATED"
    assert out.education[0].school_name == "대진대학교"
    assert out.education[0].degree == "학사"
    assert out.education[0].source_refs[0].quote_text == "대진대학교 졸업"


def test_graduated_school_only_quote_dropped() -> None:
    from app.modules.analysis.compact_v8 import (
        clear_education_status_without_literal_evidence,
    )

    doc = _edu_doc(status="GRADUATED", quote="대진대학교")
    out = clear_education_status_without_literal_evidence(doc)
    row = out.education[0]
    assert row.status is None
    assert row.school_name == "대진대학교"
    assert row.major == "도시공학과"
    assert row.degree == "학사"
    assert row.source_refs[0].quote_text == "대진대학교"


def test_graduated_degree_only_quote_dropped() -> None:
    from app.modules.analysis.compact_v8 import (
        clear_education_status_without_literal_evidence,
    )

    doc = _edu_doc(status="GRADUATED", quote="학사", degree="학사")
    out = clear_education_status_without_literal_evidence(doc)
    assert out.education[0].status is None
    assert out.education[0].degree == "학사"


def test_unknown_status_fail_closed() -> None:
    from app.modules.analysis.compact_v8 import (
        clear_education_status_without_literal_evidence,
    )

    doc = _edu_doc(status="ALUMNI", quote="대진대학교 졸업")
    out = clear_education_status_without_literal_evidence(doc)
    assert out.education[0].status is None


def test_korean_jolup_status_with_literal_kept() -> None:
    from app.modules.analysis.compact_v8 import (
        clear_education_status_without_literal_evidence,
    )

    doc = _edu_doc(status="졸업", quote="졸업")
    out = clear_education_status_without_literal_evidence(doc)
    assert out.education[0].status == "졸업"


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.require_literal_education_status_evidence is False
    assert not hasattr(v19, "REQUIRE_LITERAL_EDUCATION_STATUS_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.REQUIRE_LITERAL_EDUCATION_STATUS_EVIDENCE is True
    assert current_profile_prompt().require_literal_education_status_evidence is True
