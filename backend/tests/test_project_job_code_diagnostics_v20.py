"""v20 PROJECT job code diagnostic helpers (counts/codes only)."""

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


def _ref(quote: str | None):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=_DOC, page_no=1, quote_text=quote)


def _doc(*, jobs: list[tuple[str, str | None]]):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C1",
                start_date="2019.07",
                end_date="2020.01",
                source_refs=[_ref("P1")],
                jobs=[
                    CodeRefCandidate(
                        code=code,
                        raw_value=code,
                        source_refs=[_ref(quote)] if quote is not None else [],
                    )
                    for code, quote in jobs
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_count_project_job_code_distribution_sorted_counts_only() -> None:
    from app.modules.analysis.compact_v8 import count_project_job_code_distribution

    doc = _doc(
        jobs=[
            ("JOB-OPS-SYS", None),
            ("JOB-ARC-TA", "TA"),
            ("JOB-OPS-SYS", None),
            ("JOB-MGT", None),
            ("JOB-OPS-SYS", None),
        ]
    )
    assert count_project_job_code_distribution(doc) == {
        "JOB-ARC-TA": 1,
        "JOB-MGT": 1,
        "JOB-OPS-SYS": 3,
    }
    assert count_project_job_code_distribution(None) == {}


def test_count_project_jobs_with_quote_evidence() -> None:
    from app.modules.analysis.compact_v8 import count_project_jobs_with_quote_evidence

    doc = _doc(
        jobs=[
            ("JOB-OPS-SYS", None),
            ("JOB-ARC-TA", "TA"),
            ("JOB-OPS-SYS", ""),
            ("JOB-MGT", "사업관리"),
        ]
    )
    assert count_project_jobs_with_quote_evidence(doc) == 2
    assert count_project_jobs_with_quote_evidence(None) == 0


def test_semantic_job_backfill_delta_for_diagnostics() -> None:
    """Helper pair supports job_semantic_backfilled_refs = after - before."""
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_project_job_evidence,
        count_project_job_code_distribution,
        count_project_jobs_with_quote_evidence,
    )

    page_texts = {
        (_DOC, 1): (
            "P1 2019.07.01 ~ 2020.01.31 C1 시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    before = _doc(jobs=[("JOB-OPS-SYS", None), ("JOB-ARC-TA", "TA")])
    before_quoted = count_project_jobs_with_quote_evidence(before)
    after = backfill_semantic_project_job_evidence(
        before,
        page_texts=page_texts,
        allowed_documents={_DOC: {1}},
    )
    after_quoted = count_project_jobs_with_quote_evidence(after)
    assert count_project_job_code_distribution(after) == {
        "JOB-ARC-TA": 1,
        "JOB-OPS-SYS": 1,
    }
    assert before_quoted == 1
    assert after_quoted == 2
    assert max(0, after_quoted - before_quoted) == 1
