"""profile-extract-v21 CORE/root JOB evidence + EXP supplement."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "e3bcbf71-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_profile_extract_registry_current_is_v21() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts import profile_extract_v21 as v21
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v21"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v21"
    assert cur.backfill_semantic_core_job_evidence is True
    assert cur.require_semantic_core_job_evidence is True
    assert cur.supplement_semantic_core_exp_from_source is True
    assert v21.BACKFILL_SEMANTIC_CORE_JOB_EVIDENCE is True
    assert v21.REQUIRE_SEMANTIC_CORE_JOB_EVIDENCE is True
    assert v21.SUPPLEMENT_SEMANTIC_CORE_EXP_FROM_SOURCE is True
    # Inherits v20 CORE/PROJECT guards.
    assert cur.backfill_semantic_core_exp_evidence is True
    assert cur.require_semantic_project_job_evidence is True
    assert cur.recover_project_recall_second_pass is True

    v20_spec = resolve_profile_prompt("profile-extract-v20")
    assert v20_spec.backfill_semantic_core_job_evidence is False
    assert v20_spec.require_semantic_core_job_evidence is False
    assert v20_spec.supplement_semantic_core_exp_from_source is False
    assert not hasattr(v20, "BACKFILL_SEMANTIC_CORE_JOB_EVIDENCE")
    assert not hasattr(v20, "REQUIRE_SEMANTIC_CORE_JOB_EVIDENCE")
    assert not hasattr(v20, "SUPPLEMENT_SEMANTIC_CORE_EXP_FROM_SOURCE")
    # PROJECT contract unchanged from v20.
    assert v21.PROJECTS_SYSTEM_PROMPT == v20.PROJECTS_SYSTEM_PROMPT
    assert v21.PROJECTS_SCHEMA_GUIDE == v20.PROJECTS_SCHEMA_GUIDE


def _core_doc(*, jobs, expertise=None, projects=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SourceRef,
        SummaryCandidate,
    )

    def _jobs(items):
        out = []
        for item in items:
            if isinstance(item, JobCandidate):
                out.append(item)
                continue
            refs = [
                SourceRef(
                    document_id=r["document_id"],
                    page_no=r["page_no"],
                    quote_text=r["quote_text"],
                )
                for r in (item.get("source_refs") or [])
            ]
            out.append(
                JobCandidate(
                    code=item["code"],
                    raw_value=item.get("raw_value") or item["code"],
                    source_refs=refs,
                )
            )
        return out

    exp_rows = []
    for item in expertise or []:
        if isinstance(item, ExpertiseCandidate):
            exp_rows.append(item)
            continue
        refs = [
            SourceRef(
                document_id=r["document_id"],
                page_no=r["page_no"],
                quote_text=r["quote_text"],
            )
            for r in (item.get("source_refs") or [])
        ]
        exp_rows.append(
            ExpertiseCandidate(
                code=item["code"],
                raw_value=item.get("raw_value") or item["code"],
                evidence_type=item.get("evidence_type") or "EXPLICIT",
                source_refs=refs,
            )
        )

    proj_rows = projects or []
    if proj_rows and isinstance(proj_rows[0], dict):
        converted = []
        for p in proj_rows:
            converted.append(
                ProjectCandidate(
                    project_name=p.get("project_name"),
                    customer_name=p.get("customer_name"),
                    start_date=p.get("start_date"),
                    end_date=p.get("end_date"),
                    jobs=[
                        CodeRefCandidate(
                            code=j["code"],
                            raw_value=j.get("raw_value") or j["code"],
                            source_refs=[
                                SourceRef(
                                    document_id=r["document_id"],
                                    page_no=r["page_no"],
                                    quote_text=r["quote_text"],
                                )
                                for r in (j.get("source_refs") or [])
                            ],
                        )
                        for j in (p.get("jobs") or [])
                    ],
                    source_refs=[
                        SourceRef(
                            document_id=r["document_id"],
                            page_no=r["page_no"],
                            quote_text=r["quote_text"],
                        )
                        for r in (p.get("source_refs") or [])
                    ],
                )
            )
        proj_rows = converted

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강태련"),
        jobs=_jobs(jobs),
        expertise=exp_rows,
        projects=proj_rows,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _apply_v21_core_guards(doc, *, page_texts, allowed_documents):
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
        backfill_semantic_core_job_evidence,
        drop_root_jobs_without_semantic_evidence,
        supplement_semantic_core_expertise_from_source,
    )

    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents=allowed_documents
    )
    out = supplement_semantic_core_expertise_from_source(
        out, page_texts=page_texts, allowed_documents=allowed_documents
    )
    out = backfill_semantic_core_job_evidence(
        out, page_texts=page_texts, allowed_documents=allowed_documents
    )
    return drop_root_jobs_without_semantic_evidence(out)


def test_gangtaeryeon_core_job_fp_fail_closed_and_exp_mgt_supplement() -> None:
    page_texts = {
        (_DOC, 1): (
            "시스템구축 및 운영 > IT시스템기술지원\n"
            "2017 사업관리\n"
            "2013 TA\n"
        )
    }
    allowed = {_DOC: {1}}
    doc = _core_doc(
        jobs=[
            {"code": "JOB-OPS-SYS"},
            {"code": "JOB-ARC-AA"},
            {"code": "JOB-ARC-SA"},
            {"code": "JOB-ARC-TA"},
            {"code": "JOB-MGT-PM"},
            {"code": "JOB-MGT-PMO"},
        ],
        expertise=[{"code": "EXP-INFRA"}],
    )
    out = _apply_v21_core_guards(
        doc, page_texts=page_texts, allowed_documents=allowed
    )
    assert {j.code for j in out.jobs} == {"JOB-OPS-SYS"}
    assert {x.code for x in out.expertise} == {"EXP-INFRA", "EXP-MGT"}
    ops = next(j for j in out.jobs if j.code == "JOB-OPS-SYS")
    assert ops.source_refs
    assert "시스템구축 및 운영" in (ops.source_refs[0].quote_text or "")
    assert "IT시스템기술지원" in (ops.source_refs[0].quote_text or "")
    mgt = next(x for x in out.expertise if x.code == "EXP-MGT")
    assert mgt.raw_value == "사업관리"
    assert mgt.evidence_type == "EXPLICIT"
    assert mgt.source_refs
    assert mgt.source_refs[0].quote_text == "사업관리"


def test_literal_pm_source_keeps_job_mgt_pm() -> None:
    page_texts = {(_DOC, 1): "PM 사업관리 수행"}
    doc = _core_doc(jobs=[{"code": "JOB-MGT-PM"}], expertise=[])
    out = _apply_v21_core_guards(
        doc, page_texts=page_texts, allowed_documents={_DOC: {1}}
    )
    assert {j.code for j in out.jobs} == {"JOB-MGT-PM"}
    assert out.jobs[0].source_refs[0].quote_text == "PM"
    assert {x.code for x in out.expertise} == {"EXP-MGT"}


def test_literal_pl_source_keeps_job_mgt_pl() -> None:
    page_texts = {(_DOC, 1): "PL 역할 수행"}
    doc = _core_doc(jobs=[{"code": "JOB-MGT-PL"}], expertise=[])
    out = _apply_v21_core_guards(
        doc, page_texts=page_texts, allowed_documents={_DOC: {1}}
    )
    assert {j.code for j in out.jobs} == {"JOB-MGT-PL"}
    assert out.jobs[0].source_refs[0].quote_text == "PL"


def test_pmo_not_treated_as_pm_evidence() -> None:
    page_texts = {(_DOC, 1): "PMO 사업관리"}
    doc = _core_doc(
        jobs=[{"code": "JOB-MGT-PM"}, {"code": "JOB-MGT-PMO"}],
        expertise=[],
    )
    out = _apply_v21_core_guards(
        doc, page_texts=page_texts, allowed_documents={_DOC: {1}}
    )
    assert {j.code for j in out.jobs} == set()
    assert {x.code for x in out.expertise} == {"EXP-MGT"}


def test_project_ta_relation_preserved() -> None:
    """TA may remain on project relations; root TA is dropped."""
    page_texts = {
        (_DOC, 1): "시스템구축 및 운영 > IT시스템기술지원\n2013 TA\n"
    }
    doc = _core_doc(
        jobs=[{"code": "JOB-OPS-SYS"}, {"code": "JOB-ARC-TA"}],
        expertise=[{"code": "EXP-INFRA"}],
        projects=[
            {
                "project_name": "2013년 차기 복권통합구축사업",
                "customer_name": "복권위원회",
                "start_date": "2013.09",
                "end_date": "2013.12",
                "jobs": [
                    {
                        "code": "JOB-ARC-TA",
                        "source_refs": [
                            {
                                "document_id": _DOC,
                                "page_no": 1,
                                "quote_text": "TA",
                            }
                        ],
                    }
                ],
                "source_refs": [
                    {
                        "document_id": _DOC,
                        "page_no": 1,
                        "quote_text": "2013년 차기 복권통합구축사업",
                    }
                ],
            }
        ],
    )
    out = _apply_v21_core_guards(
        doc, page_texts=page_texts, allowed_documents={_DOC: {1}}
    )
    assert {j.code for j in out.jobs} == {"JOB-OPS-SYS"}
    assert len(out.projects) == 1
    assert {j.code for j in out.projects[0].jobs} == {"JOB-ARC-TA"}
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "TA"


def test_existing_unsupported_root_job_quote_still_dropped() -> None:
    """Even with a TA quote ref, root JOB-ARC-TA is not kept."""
    doc = _core_doc(
        jobs=[
            {
                "code": "JOB-ARC-TA",
                "source_refs": [
                    {
                        "document_id": _DOC,
                        "page_no": 1,
                        "quote_text": "TA",
                    }
                ],
            }
        ],
        expertise=[],
    )
    out = _apply_v21_core_guards(
        doc,
        page_texts={(_DOC, 1): "TA"},
        allowed_documents={_DOC: {1}},
    )
    assert out.jobs == []


def test_unrelated_root_job_taxonomy_preserved() -> None:
    """v21 guard must not drop unrelated catalog root JOBs."""
    preserved = [
        "JOB-DATA-DBA",
        "JOB-DEV-BE",
        "JOB-AI-LLM",
        "JOB-SYS-SE",
        "JOB-SEC-ENG",
        "JOB-OPS-APP",
        "JOB-QA-ENG",
    ]
    forbidden = [
        "JOB-ARC-AA",
        "JOB-ARC-SA",
        "JOB-ARC-TA",
        "JOB-MGT-PMO",
        "JOB-MGT",
    ]
    doc = _core_doc(
        jobs=[{"code": c} for c in preserved + forbidden],
        expertise=[],
    )
    out = _apply_v21_core_guards(
        doc,
        page_texts={(_DOC, 1): "관련 없는 텍스트"},
        allowed_documents={_DOC: {1}},
    )
    assert {j.code for j in out.jobs} == set(preserved)
    assert not ({j.code for j in out.jobs} & set(forbidden))


def test_v20_core_job_helpers_not_applied_when_flags_false() -> None:
    """v20 path leaves FP root jobs when v21 flags are off."""
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v20 = resolve_profile_prompt("profile-extract-v20")
    assert v20.backfill_semantic_core_job_evidence is False
    assert v20.require_semantic_core_job_evidence is False
    assert v20.supplement_semantic_core_exp_from_source is False

    page_texts = {
        (_DOC, 1): "시스템구축 및 운영 > IT시스템기술지원\n사업관리\n"
    }
    doc = _core_doc(
        jobs=[
            {"code": "JOB-OPS-SYS"},
            {"code": "JOB-ARC-TA"},
            {"code": "JOB-MGT-PM"},
        ],
        expertise=[{"code": "EXP-INFRA"}],
    )
    # Only v20 EXP backfill runs.
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC: {1}}
    )
    assert {j.code for j in out.jobs} == {
        "JOB-OPS-SYS",
        "JOB-ARC-TA",
        "JOB-MGT-PM",
    }
    assert {x.code for x in out.expertise} == {"EXP-INFRA"}
