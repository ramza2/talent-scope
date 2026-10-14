"""v20 PROJECT JOB-MGT-PM/PL literal quote-token evidence guard."""

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


def _project_doc(*, jobs: list[dict], skills=None, expertise=None):
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
                start_date="2018.06",
                end_date="2019.01",
                source_refs=[_ref("P1")],
                jobs=[CodeRefCandidate(**j) for j in jobs],
                skills=[
                    CodeRefCandidate(**s)
                    for s in (skills or [{"code": "TECH-DB-ORACLE", "raw_value": "Oracle", "source_refs": [_ref("Oracle")]}])
                ],
                expertise=[
                    CodeRefCandidate(**e)
                    for e in (
                        expertise
                        or [
                            {
                                "code": "EXP-MGT",
                                "raw_value": "사업관리",
                                "source_refs": [_ref("사업관리")],
                            }
                        ]
                    )
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_pm_literal_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "PM 사업관리",
                "source_refs": [_ref("PM 사업관리")],
            }
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    assert any(j.code == "JOB-MGT-PM" for j in out.projects[0].jobs)
    assert out.projects[0].skills
    assert out.projects[0].expertise


def test_pm_bujeong_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "PM",
                "source_refs": [_ref("부장")],
            }
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    assert not any(j.code == "JOB-MGT-PM" for j in out.projects[0].jobs)
    assert out.projects[0].project_name == "P1"
    assert out.projects[0].skills[0].code == "TECH-DB-ORACLE"
    assert out.projects[0].expertise[0].code == "EXP-MGT"


def test_pm_saeopgwanri_alone_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "사업관리",
                "source_refs": [_ref("사업관리")],
            }
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    assert not any(j.code == "JOB-MGT-PM" for j in out.projects[0].jobs)


def test_pmo_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "PMO",
                "source_refs": [_ref("PMO")],
            }
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    assert not any(j.code == "JOB-MGT-PM" for j in out.projects[0].jobs)


def test_pl_literal_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PL",
                "raw_value": "PL",
                "source_refs": [_ref("PL")],
            }
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    assert any(j.code == "JOB-MGT-PL" for j in out.projects[0].jobs)


def test_ta_unaffected() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-ARC-TA",
                "raw_value": "TA",
                "source_refs": [_ref("TA")],
            },
            {
                "code": "JOB-MGT-PM",
                "raw_value": "PM",
                "source_refs": [_ref("부장")],
            },
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    codes = {j.code for j in out.projects[0].jobs}
    assert "JOB-ARC-TA" in codes
    assert "JOB-MGT-PM" not in codes


def test_v19_flag_false_and_no_attr() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.require_literal_pm_pl_project_job_evidence is False
    assert not hasattr(v19, "REQUIRE_LITERAL_PM_PL_PROJECT_JOB_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.REQUIRE_LITERAL_PM_PL_PROJECT_JOB_EVIDENCE is True
    assert current_profile_prompt().require_literal_pm_pl_project_job_evidence is True


def test_bracket_and_slash_pm_pl_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    doc = _project_doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "PM",
                "source_refs": [_ref("[PM]")],
            },
            {
                "code": "JOB-MGT-PL",
                "raw_value": "PL",
                "source_refs": [_ref("PM/PL")],
            },
        ]
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(doc)
    codes = {j.code for j in out.projects[0].jobs}
    assert codes == {"JOB-MGT-PM", "JOB-MGT-PL"}
