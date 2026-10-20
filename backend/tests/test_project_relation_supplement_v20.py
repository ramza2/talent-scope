"""Supplement missing PROJECT JOB/EXP from unique same-project evidence."""

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


def _ref(quote: str, *, page_no: int = 1):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=_DOC, page_no=page_no, quote_text=quote)


def _project(
    *,
    name: str,
    customer: str,
    start: str,
    end: str,
    jobs=None,
    expertise=None,
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    def _rels(items):
        out = []
        for code, quote in items or []:
            out.append(
                CodeRefCandidate(
                    code=code,
                    raw_value=code,
                    source_refs=[_ref(quote)] if quote else [],
                )
            )
        return out

    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[_ref(name)],
        jobs=_rels(jobs),
        expertise=_rels(expertise),
    )


def _doc(projects, page_texts):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
        drop_project_jobs_without_literal_pm_pl_evidence,
        drop_project_jobs_without_semantic_evidence,
        supplement_semantic_project_relations_from_source,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강상원"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    allowed = {_DOC: {page for (_d, page) in page_texts}}
    out = supplement_semantic_project_relations_from_source(
        candidate, page_texts=page_texts, allowed_documents=allowed
    )
    out = drop_project_jobs_without_semantic_evidence(out)
    out = drop_project_expertise_without_semantic_evidence(out)
    return drop_project_jobs_without_literal_pm_pl_evidence(out)


def test_flag_default_false_on_v19_true_on_v20() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.ai.prompts import profile_extract_v20 as v20

    assert resolve_profile_prompt(
        "profile-extract-v19"
    ).supplement_semantic_project_relations_from_source is False
    assert v20.SUPPLEMENT_SEMANTIC_PROJECT_RELATIONS_FROM_SOURCE is True
    assert resolve_profile_prompt(
        "profile-extract-v20"
    ).supplement_semantic_project_relations_from_source is True


def test_same_project_missing_job_exp_supplemented() -> None:
    page_texts = {
        (_DOC, 1): (
            "정보시스템1군 운영사업 2017.03 ~ 2017.12 행정안전부 "
            "정보시스템1군 운영 사업관리"
        )
    }
    out = _doc(
        [
            _project(
                name="정보시스템1군 운영사업",
                customer="행정안전부",
                start="2017.03",
                end="2017.12",
            )
        ],
        page_texts,
    )
    jobs = {j.code for j in out.projects[0].jobs}
    exp = {x.code for x in out.projects[0].expertise}
    assert "JOB-OPS-SYS" in jobs
    assert "JOB-MGT" in jobs
    assert "EXP-INFRA" in exp
    assert "EXP-MGT" in exp
    ops = next(j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS")
    assert ops.source_refs[0].quote_text == "정보시스템1군 운영"
    assert ops.source_refs[0].document_id == _DOC
    mgt = next(x for x in out.projects[0].expertise if x.code == "EXP-MGT")
    assert mgt.raw_value == "사업관리"
    assert mgt.source_refs[0].quote_text == "사업관리"


def test_sec_ops_and_sec_build_phrases_supplemented() -> None:
    page_texts = {
        (_DOC, 1): (
            "정보보안시스템 운영사업 2018.01 ~ 2018.06 국가정보원 "
            "정보보안시스템 운영\n"
            "정보보호 강화사업 2019.02 ~ 2019.11 행정안전부 "
            "정보보호 강화(HW)+구축"
        )
    }
    out = _doc(
        [
            _project(
                name="정보보안시스템 운영사업",
                customer="국가정보원",
                start="2018.01",
                end="2018.06",
            ),
            _project(
                name="정보보호 강화사업",
                customer="행정안전부",
                start="2019.02",
                end="2019.11",
            ),
        ],
        page_texts,
    )
    assert {x.code for x in out.projects[0].expertise} == {"EXP-SEC-OPS"}
    assert out.projects[0].expertise[0].source_refs[0].quote_text == (
        "정보보안시스템 운영"
    )
    assert {x.code for x in out.projects[1].expertise} == {"EXP-SEC-BUILD"}
    assert "정보보호 강화" in (
        out.projects[1].expertise[0].source_refs[0].quote_text or ""
    )
    assert "구축" in (
        out.projects[1].expertise[0].source_refs[0].quote_text or ""
    )


def test_pptx_combined_line_does_not_mix_other_project_duties() -> None:
    """Duties on a shared PPTX line must stay within each project's span."""
    page_texts = {
        (_DOC, 1): (
            "A운영사업 2017.01.01 ~ 2017.12.31 고객A 정보시스템1군 운영 "
            "B보안사업 2018.01.01 ~ 2018.12.31 고객B 정보보안시스템 운영"
        )
    }
    out = _doc(
        [
            _project(
                name="A운영사업",
                customer="고객A",
                start="2017.01",
                end="2017.12",
            ),
            _project(
                name="B보안사업",
                customer="고객B",
                start="2018.01",
                end="2018.12",
            ),
        ],
        page_texts,
    )
    a_jobs = {j.code for j in out.projects[0].jobs}
    a_exp = {x.code for x in out.projects[0].expertise}
    b_jobs = {j.code for j in out.projects[1].jobs}
    b_exp = {x.code for x in out.projects[1].expertise}
    assert a_jobs == {"JOB-OPS-SYS"}
    assert a_exp == {"EXP-INFRA"}
    assert "EXP-SEC-OPS" not in a_exp
    assert b_exp == {"EXP-SEC-OPS"}
    assert "JOB-OPS-SYS" not in b_jobs
    assert "EXP-INFRA" not in b_exp


def test_ambiguous_multi_project_match_no_supplement() -> None:
    page_texts = {
        (_DOC, 1): (
            "공통사업 2017.03 ~ 2017.12 고객A 정보시스템1군 운영\n"
            "공통사업 2017.03 ~ 2017.12 고객B 정보시스템1군 운영"
        )
    }
    # Same name+start appears twice with different customers → customer path
    # also ambiguous when name is shared; fail closed.
    out = _doc(
        [
            _project(
                name="공통사업",
                customer="고객A",
                start="2017.03",
                end="2017.12",
            )
        ],
        page_texts,
    )
    # Name matches two rows → no unique owned span for supplement.
    assert out.projects[0].jobs == []
    assert out.projects[0].expertise == []


def test_plain_ops_build_words_alone_do_not_supplement() -> None:
    page_texts = {
        (_DOC, 1): "단순유지사업 2017.03 ~ 2017.12 고객A 운영 구축 유지관리"
    }
    out = _doc(
        [
            _project(
                name="단순유지사업",
                customer="고객A",
                start="2017.03",
                end="2017.12",
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs == []
    assert out.projects[0].expertise == []


def test_existing_relation_and_refs_preserved() -> None:
    page_texts = {
        (_DOC, 1): (
            "운영사업 2017.03 ~ 2017.12 고객A 정보시스템1군 운영 사업관리"
        )
    }
    existing_quote = "기존OPS근거"
    out = _doc(
        [
            _project(
                name="운영사업",
                customer="고객A",
                start="2017.03",
                end="2017.12",
                jobs=[("JOB-OPS-SYS", existing_quote)],
                expertise=[("EXP-INFRA", "정보시스템1군 운영")],
            )
        ],
        page_texts,
    )
    ops = next(j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS")
    assert ops.source_refs[0].quote_text == existing_quote
    assert len([j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS"]) == 1
    assert len([x for x in out.projects[0].expertise if x.code == "EXP-INFRA"]) == 1
    # Missing MGT still supplemented.
    assert "JOB-MGT" in {j.code for j in out.projects[0].jobs}
    assert "EXP-MGT" in {x.code for x in out.projects[0].expertise}


def test_gangtaeryeon_style_ops_infra_mgt_ta_still_work() -> None:
    """Regression: 강태련-style OPS/INFRA + 사업관리 + TA remain recoverable."""
    page_texts = {
        (_DOC, 1): (
            "2011년 제2차 자원통합 구축 HW1 사업(대전) "
            "2011.06.10 ~ 2011.12.24 정부통합전산센터 "
            "시스템구축 및 운영 > IT시스템기술지원\n"
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 복권위원회 "
            "TA 사업관리"
        )
    }
    out = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            ),
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="복권위원회",
                start="2013.09",
                end="2013.12",
            ),
        ],
        page_texts,
    )
    p2011 = out.projects[0]
    assert {j.code for j in p2011.jobs} == {"JOB-OPS-SYS"}
    assert {x.code for x in p2011.expertise} == {"EXP-INFRA"}
    p2013 = out.projects[1]
    assert {j.code for j in p2013.jobs} >= {"JOB-ARC-TA", "JOB-MGT"}
    assert "EXP-MGT" in {x.code for x in p2013.expertise}
