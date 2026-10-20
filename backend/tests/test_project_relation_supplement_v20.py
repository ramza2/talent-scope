"""Supplement/backfill PROJECT JOB/EXP from unique same-project evidence."""

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


def _run_supplement(projects, page_texts):
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
    # Supplement always uses enhanced semantics; guards must match.
    out = drop_project_jobs_without_semantic_evidence(
        out, enhanced_project_relation_semantics=True
    )
    out = drop_project_expertise_without_semantic_evidence(
        out, enhanced_project_relation_semantics=True
    )
    return drop_project_jobs_without_literal_pm_pl_evidence(out)


def _run_backfill(projects, page_texts, *, enhanced: bool = False):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_project_expertise_evidence,
        backfill_semantic_project_job_evidence,
        drop_project_expertise_without_semantic_evidence,
        drop_project_jobs_without_literal_pm_pl_evidence,
        drop_project_jobs_without_semantic_evidence,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강상원"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    allowed = {_DOC: {page for (_d, page) in page_texts}}
    out = backfill_semantic_project_expertise_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents=allowed,
        enhanced_project_relation_semantics=enhanced,
    )
    out = backfill_semantic_project_job_evidence(
        out,
        page_texts=page_texts,
        allowed_documents=allowed,
        enhanced_project_relation_semantics=enhanced,
    )
    out = drop_project_jobs_without_semantic_evidence(
        out, enhanced_project_relation_semantics=enhanced
    )
    out = drop_project_expertise_without_semantic_evidence(
        out, enhanced_project_relation_semantics=enhanced
    )
    return drop_project_jobs_without_literal_pm_pl_evidence(out)


def test_flag_default_false_on_v19_v20_v21_true_on_v22() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts import profile_extract_v21 as v21
    from app.ai.prompts import profile_extract_v22 as v22

    assert resolve_profile_prompt(
        "profile-extract-v19"
    ).supplement_semantic_project_relations_from_source is False
    assert v20.SUPPLEMENT_SEMANTIC_PROJECT_RELATIONS_FROM_SOURCE is False
    assert resolve_profile_prompt(
        "profile-extract-v20"
    ).supplement_semantic_project_relations_from_source is False
    assert v21.SUPPLEMENT_SEMANTIC_PROJECT_RELATIONS_FROM_SOURCE is False
    assert resolve_profile_prompt(
        "profile-extract-v21"
    ).supplement_semantic_project_relations_from_source is False
    assert v22.SUPPLEMENT_SEMANTIC_PROJECT_RELATIONS_FROM_SOURCE is True
    assert resolve_profile_prompt(
        "profile-extract-v22"
    ).supplement_semantic_project_relations_from_source is True


def test_core_exp_patterns_do_not_include_project_only_phrases() -> None:
    from app.modules.analysis.compact_v8 import (
        _EXP_SEMANTIC_PATTERNS,
        _PROJECT_EXP_SEMANTIC_PATTERNS,
        _quote_supports_semantic_exp,
        _quote_supports_semantic_project_exp,
    )

    core_infra = " ".join(
        phrase for _s, phrase, _c in _EXP_SEMANTIC_PATTERNS["EXP-INFRA"]
    )
    assert "정보시스템1군 운영" not in core_infra
    assert "정보시스템1군 운영" in " ".join(
        phrase for _s, phrase, _c in _PROJECT_EXP_SEMANTIC_PATTERNS["EXP-INFRA"]
    )
    assert _quote_supports_semantic_exp("EXP-INFRA", "정보시스템1군 운영") is False
    assert (
        _quote_supports_semantic_project_exp("EXP-INFRA", "정보시스템1군 운영")
        is True
    )
    # CORE keeps simple substring match (보안시스템 운영 still hits 시스템 운영).
    assert _quote_supports_semantic_exp("EXP-INFRA", "정보보안시스템 운영") is True
    # PROJECT-enhanced rejects INFRA for *보안시스템 운영 tails.
    assert (
        _quote_supports_semantic_project_exp("EXP-INFRA", "정보보안시스템 운영")
        is False
    )
    assert _quote_supports_semantic_exp("EXP-SEC-OPS", "정보보안시스템 운영") is False
    assert (
        _quote_supports_semantic_project_exp("EXP-SEC-OPS", "정보보안시스템 운영")
        is True
    )


def test_same_project_missing_job_exp_supplemented() -> None:
    page_texts = {
        (_DOC, 1): (
            "정보시스템1군 운영사업 | 개요 | 2017.03~2017.12 | "
            "정보시스템1군 운영 사업관리 | 행정안전부"
        )
    }
    out = _run_supplement(
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


_GANGSANGWON_LIVE_ROWS = (
    "2022년 국가정보자원 관리원 광주센터 정보시스템1군 운영 | 운영 | "
    "2022.01~2023.01 | 운영 | 국가정보자원 관리원(광주) "
    "2019년 국가정보자원 관리원 광주센터 정보시스템1군 운영 | 운영 | "
    "2019.01~2021.12 | 운영 | 국가정보자원 관리원(광주) "
    "2018년 국가정보자원 관리원 광주센터 정보시스템1군 운영 | 운영 | "
    "2018.01~2018.12 | 운영 | 국가정보자원 관리원(광주) "
    "광주통합전산센터 2015년 정보보호 강화(HW) | 구축 | "
    "2015.07~2015.12 | 구축, 사업관리 | 국가정보자원 관리원(광주) "
    "현대/기아자동차 정보보안시스템 운영 | 운영 | "
    "2012.07~2015.02 | PL 운영 | 현대오토에버"
)


def _gangsangwon_live_projects(*, with_bad_quotes: bool):
    """Exact 강상원 PPTX project identities used in live career rows."""
    bad = "업무분석" if with_bad_quotes else None
    return [
        _project(
            name="2022년 국가정보자원 관리원 광주센터 정보시스템1군 운영",
            customer="국가정보자원 관리원(광주)",
            start="2022.01",
            end="2023.01",
            jobs=[("JOB-OPS-SYS", bad)] if with_bad_quotes else None,
            expertise=[("EXP-INFRA", bad)] if with_bad_quotes else None,
        ),
        _project(
            name="2019년 국가정보자원 관리원 광주센터 정보시스템1군 운영",
            customer="국가정보자원 관리원(광주)",
            start="2019.01",
            end="2021.12",
            jobs=[("JOB-OPS-SYS", bad)] if with_bad_quotes else None,
            expertise=[("EXP-INFRA", bad)] if with_bad_quotes else None,
        ),
        _project(
            name="2018년 국가정보자원 관리원 광주센터 정보시스템1군 운영",
            customer="국가정보자원 관리원(광주)",
            start="2018.01",
            end="2018.12",
            jobs=[("JOB-OPS-SYS", bad)] if with_bad_quotes else None,
            expertise=[("EXP-INFRA", bad)] if with_bad_quotes else None,
        ),
        _project(
            name="광주통합전산센터 2015년 정보보호 강화(HW)",
            customer="국가정보자원 관리원(광주)",
            start="2015.07",
            end="2015.12",
            jobs=[("JOB-MGT", bad)] if with_bad_quotes else None,
            expertise=(
                [("EXP-MGT", bad), ("EXP-SEC-BUILD", bad)]
                if with_bad_quotes
                else None
            ),
        ),
        _project(
            name="현대/기아자동차 정보보안시스템 운영",
            customer="현대오토에버",
            start="2012.07",
            end="2015.02",
            jobs=(
                [("JOB-MGT-PL", bad), ("JOB-OPS-SYS", bad)]
                if with_bad_quotes
                else None
            ),
            expertise=[("EXP-SEC-OPS", bad)] if with_bad_quotes else None,
        ),
    ]


def test_gangsangwon_live_pptx_title_duty_backfill() -> None:
    """Exact live rows: title phrase + duty token recovers PROJECT JOB/EXP."""
    page_texts = {(_DOC, 1): _GANGSANGWON_LIVE_ROWS}
    out = _run_backfill(
        _gangsangwon_live_projects(with_bad_quotes=True),
        page_texts,
        enhanced=True,
    )

    for idx in (0, 1, 2):
        assert {j.code for j in out.projects[idx].jobs} == {"JOB-OPS-SYS"}
        assert {x.code for x in out.projects[idx].expertise} == {"EXP-INFRA"}
        assert out.projects[idx].jobs[0].source_refs[0].quote_text == (
            "정보시스템1군 운영"
        )
        assert out.projects[idx].expertise[0].source_refs[0].quote_text == (
            "정보시스템1군 운영"
        )

    p2015 = out.projects[3]
    assert {j.code for j in p2015.jobs} == {"JOB-MGT"}
    assert {x.code for x in p2015.expertise} == {"EXP-MGT", "EXP-SEC-BUILD"}
    assert any(
        "정보보호 강화" in (x.source_refs[0].quote_text or "")
        and "구축" in (x.source_refs[0].quote_text or "")
        for x in p2015.expertise
        if x.code == "EXP-SEC-BUILD"
    )
    assert next(
        x for x in p2015.expertise if x.code == "EXP-MGT"
    ).source_refs[0].quote_text == "사업관리"
    assert "JOB-OPS-SYS" not in {j.code for j in p2015.jobs}
    assert "EXP-INFRA" not in {x.code for x in p2015.expertise}

    p_sec = out.projects[4]
    assert {j.code for j in p_sec.jobs} == {"JOB-MGT-PL", "JOB-OPS-SYS"}
    assert next(
        j for j in p_sec.jobs if j.code == "JOB-MGT-PL"
    ).source_refs[0].quote_text == "PL"
    assert next(
        j for j in p_sec.jobs if j.code == "JOB-OPS-SYS"
    ).source_refs[0].quote_text == "정보보안시스템 운영"
    assert {x.code for x in p_sec.expertise} == {"EXP-SEC-OPS"}
    assert p_sec.expertise[0].source_refs[0].quote_text == "정보보안시스템 운영"
    assert "EXP-INFRA" not in {x.code for x in p_sec.expertise}


def test_gangsangwon_live_pptx_title_duty_supplement() -> None:
    """Missing relations are appended from title+duty on exact live rows."""
    page_texts = {(_DOC, 1): _GANGSANGWON_LIVE_ROWS}
    out = _run_supplement(
        _gangsangwon_live_projects(with_bad_quotes=False), page_texts
    )

    for idx in (0, 1, 2):
        assert {j.code for j in out.projects[idx].jobs} == {"JOB-OPS-SYS"}
        assert {x.code for x in out.projects[idx].expertise} == {"EXP-INFRA"}

    p2015 = out.projects[3]
    assert {j.code for j in p2015.jobs} == {"JOB-MGT"}
    assert {x.code for x in p2015.expertise} == {"EXP-MGT", "EXP-SEC-BUILD"}

    p_sec = out.projects[4]
    assert {j.code for j in p_sec.jobs} == {"JOB-MGT-PL", "JOB-OPS-SYS"}
    assert {x.code for x in p_sec.expertise} == {"EXP-SEC-OPS"}
    assert "EXP-INFRA" not in {x.code for x in p_sec.expertise}
    assert next(
        j for j in p_sec.jobs if j.code == "JOB-MGT-PL"
    ).source_refs[0].quote_text == "PL"
    assert next(
        j for j in p_sec.jobs if j.code == "JOB-OPS-SYS"
    ).source_refs[0].quote_text == "정보보안시스템 운영"


def test_title_phrase_without_duty_corroboration_no_relation() -> None:
    """Title/overview 운영 alone must not create OPS/INFRA without duty support."""
    page_texts = {
        (_DOC, 1): (
            "2022년 국가정보자원 관리원 광주센터 정보시스템1군 운영 | 운영 | "
            "2022.01~2023.01 | 유지관리 | 국가정보자원 관리원(광주)"
        )
    }
    projects = [
        _project(
            name="2022년 국가정보자원 관리원 광주센터 정보시스템1군 운영",
            customer="국가정보자원 관리원(광주)",
            start="2022.01",
            end="2023.01",
        )
    ]
    supplemented = _run_supplement(projects, page_texts)
    assert supplemented.projects[0].jobs == []
    assert supplemented.projects[0].expertise == []

    repaired = _run_backfill(
        [
            _project(
                name="2022년 국가정보자원 관리원 광주센터 정보시스템1군 운영",
                customer="국가정보자원 관리원(광주)",
                start="2022.01",
                end="2023.01",
                jobs=[("JOB-OPS-SYS", "업무분석")],
                expertise=[("EXP-INFRA", "업무분석")],
            )
        ],
        page_texts,
        enhanced=True,
    )
    assert repaired.projects[0].jobs == []
    assert repaired.projects[0].expertise == []


def test_flag_off_keeps_v20_baseline_without_project_only_recovery() -> None:
    """SUPPLEMENT=False: PROJECT-only phrases / title+duty stay inactive.

    CORE whole-line matching may still hit weak ``시스템 운영`` inside
    ``정보시스템1군 운영``; that is intentional baseline behavior. Assert that
    PROJECT-only quotes/codes (정보시스템1군 / 정보보호 강화 / 정보보안시스템)
    are not used.
    """
    page_texts = {(_DOC, 1): _GANGSANGWON_LIVE_ROWS}
    out = _run_backfill(
        _gangsangwon_live_projects(with_bad_quotes=True),
        page_texts,
        enhanced=False,
    )
    for idx in (0, 1, 2):
        for rel in list(out.projects[idx].jobs) + list(out.projects[idx].expertise):
            quote = (rel.source_refs[0].quote_text or "") if rel.source_refs else ""
            assert "정보시스템1군" not in quote
    # 정보보호 강화(+구축) is PROJECT-only; CORE has no SEC-BUILD recovery here.
    assert "EXP-SEC-BUILD" not in {x.code for x in out.projects[3].expertise}
    for rel in out.projects[3].expertise:
        quote = (rel.source_refs[0].quote_text or "") if rel.source_refs else ""
        assert "정보보호 강화" not in quote
    # 정보보안시스템 운영 is PROJECT-only SEC-OPS.
    assert "EXP-SEC-OPS" not in {x.code for x in out.projects[4].expertise}
    for rel in out.projects[4].expertise:
        quote = (rel.source_refs[0].quote_text or "") if rel.source_refs else ""
        assert "정보보안시스템" not in quote


def test_flag_off_no_title_duty_sec_build_on_single_row() -> None:
    """Title 정보보호 강화 + duty 구축 must not recover SEC-BUILD when flag off."""
    page_texts = {
        (_DOC, 1): (
            "광주통합전산센터 2015년 정보보호 강화(HW) | 구축 | "
            "2015.07~2015.12 | 구축, 사업관리 | 국가정보자원 관리원(광주)"
        )
    }
    projects = [
        _project(
            name="광주통합전산센터 2015년 정보보호 강화(HW)",
            customer="국가정보자원 관리원(광주)",
            start="2015.07",
            end="2015.12",
            jobs=[("JOB-MGT", "업무분석")],
            expertise=[
                ("EXP-MGT", "업무분석"),
                ("EXP-SEC-BUILD", "업무분석"),
            ],
        )
    ]
    off = _run_backfill(projects, page_texts, enhanced=False)
    assert "EXP-SEC-BUILD" not in {x.code for x in off.projects[0].expertise}
    # CORE still repairs 사업관리.
    assert {j.code for j in off.projects[0].jobs} == {"JOB-MGT"}
    assert {x.code for x in off.projects[0].expertise} == {"EXP-MGT"}

    on = _run_backfill(projects, page_texts, enhanced=True)
    assert {x.code for x in on.projects[0].expertise} == {
        "EXP-MGT",
        "EXP-SEC-BUILD",
    }


def test_flag_off_core_duty_local_backfill_still_works() -> None:
    """Supplement off: CORE duty-local phrases still backfill on whole lines."""
    page_texts = {
        (_DOC, 1): (
            "정보시스템 운영사업 | 개요 | 2017.03~2017.12 | "
            "정보시스템 운영 | 행정안전부"
        )
    }
    out = _run_backfill(
        [
            _project(
                name="정보시스템 운영사업",
                customer="행정안전부",
                start="2017.03",
                end="2017.12",
                jobs=[("JOB-OPS-SYS", "업무분석")],
                expertise=[("EXP-INFRA", "업무분석")],
            )
        ],
        page_texts,
        enhanced=False,
    )
    assert {j.code for j in out.projects[0].jobs} == {"JOB-OPS-SYS"}
    assert {x.code for x in out.projects[0].expertise} == {"EXP-INFRA"}
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "정보시스템 운영"
    assert out.projects[0].expertise[0].source_refs[0].quote_text == (
        "정보시스템 운영"
    )


def test_bad_existing_exp_quote_repaired_or_dropped() -> None:
    page_texts = {
        (_DOC, 1): (
            "정보시스템1군 운영사업 | 개요 | 2017.03~2017.12 | "
            "정보시스템1군 운영 | 행정안전부"
        )
    }
    # Wrong quote on existing EXP → repaired via enhanced backfill.
    repaired = _run_backfill(
        [
            _project(
                name="정보시스템1군 운영사업",
                customer="행정안전부",
                start="2017.03",
                end="2017.12",
                expertise=[("EXP-INFRA", "업무분석")],
            )
        ],
        page_texts,
        enhanced=True,
    )
    assert len(repaired.projects[0].expertise) == 1
    assert repaired.projects[0].expertise[0].code == "EXP-INFRA"
    assert repaired.projects[0].expertise[0].source_refs[0].quote_text == (
        "정보시스템1군 운영"
    )

    # No recoverable source evidence → drop.
    dropped = _run_backfill(
        [
            _project(
                name="정보시스템1군 운영사업",
                customer="행정안전부",
                start="2017.03",
                end="2017.12",
                expertise=[("EXP-INFRA", "업무분석")],
            )
        ],
        {(_DOC, 1): "정보시스템1군 운영사업 | 개요 | 2017.03~2017.12 | 유지관리 | 행정안전부"},
        enhanced=True,
    )
    assert dropped.projects[0].expertise == []


def test_pptx_combined_line_does_not_mix_other_project_duties() -> None:
    page_texts = {
        (_DOC, 1): (
            "A운영사업 | 개요 | 2017.01.01~2017.12.31 | 정보시스템1군 운영 | 고객A "
            "B보안사업 | 개요 | 2018.01.01~2018.12.31 | 정보보안시스템 운영 | 고객B"
        )
    }
    out = _run_supplement(
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
    # SEC duty evidences JOB-OPS-SYS + EXP-SEC-OPS; never EXP-INFRA.
    assert b_jobs == {"JOB-OPS-SYS"}
    assert b_exp == {"EXP-SEC-OPS"}
    assert "EXP-INFRA" not in b_exp
    assert "EXP-SEC-OPS" not in a_exp


def test_ambiguous_multi_project_match_no_supplement() -> None:
    page_texts = {
        (_DOC, 1): (
            "공통사업 | 개요 | 2017.03~2017.12 | 정보시스템1군 운영 | 고객A\n"
            "공통사업 | 개요 | 2017.03~2017.12 | 정보시스템1군 운영 | 고객B"
        )
    }
    out = _run_supplement(
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
    assert out.projects[0].jobs == []
    assert out.projects[0].expertise == []


def test_plain_ops_build_words_alone_do_not_supplement() -> None:
    page_texts = {
        (_DOC, 1): (
            "단순유지사업 | 개요 | 2017.03~2017.12 | 운영 구축 유지관리 | 고객A"
        )
    }
    out = _run_supplement(
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
            "운영사업 | 개요 | 2017.03~2017.12 | "
            "정보시스템1군 운영 사업관리 | 고객A"
        )
    }
    existing_quote = "정보시스템1군 운영"
    out = _run_supplement(
        [
            _project(
                name="운영사업",
                customer="고객A",
                start="2017.03",
                end="2017.12",
                jobs=[("JOB-OPS-SYS", existing_quote)],
                expertise=[("EXP-INFRA", existing_quote)],
            )
        ],
        page_texts,
    )
    ops = next(j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS")
    assert ops.source_refs[0].quote_text == existing_quote
    assert len([j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS"]) == 1
    assert len([x for x in out.projects[0].expertise if x.code == "EXP-INFRA"]) == 1
    assert "JOB-MGT" in {j.code for j in out.projects[0].jobs}
    assert "EXP-MGT" in {x.code for x in out.projects[0].expertise}


def test_pm_saupgwanri_does_not_duplicate_job_mgt() -> None:
    """``PM 사업관리`` → JOB-MGT-PM only; no extra JOB-MGT without standalone duty."""
    page_texts = {
        (_DOC, 1): (
            "한국가스안전공사 정보시스템 운영 | 개요 | 2016.01~2016.12 | "
            "PM 사업관리 | 한국가스안전공사"
        )
    }
    out = _run_supplement(
        [
            _project(
                name="한국가스안전공사 정보시스템 운영",
                customer="한국가스안전공사",
                start="2016.01",
                end="2016.12",
            )
        ],
        page_texts,
    )
    jobs = {j.code for j in out.projects[0].jobs}
    assert "JOB-MGT-PM" in jobs
    assert "JOB-MGT" not in jobs
    # Title 정보시스템 운영 + duty 운영 is absent (duty is PM 사업관리) → no OPS
    # from duty 운영; title phrase needs duty 운영 corroboration.
    assert "JOB-OPS-SYS" not in jobs


def test_standalone_saupgwanri_keeps_job_mgt() -> None:
    """Plain duty 사업관리 (정보보호 강화) still supplements JOB-MGT."""
    page_texts = {
        (_DOC, 1): (
            "광주통합전산센터 2015년 정보보호 강화(HW) | 구축 | "
            "2015.07~2015.12 | 구축, 사업관리 | 국가정보자원 관리원(광주)"
        )
    }
    out = _run_supplement(
        [
            _project(
                name="광주통합전산센터 2015년 정보보호 강화(HW)",
                customer="국가정보자원 관리원(광주)",
                start="2015.07",
                end="2015.12",
            )
        ],
        page_texts,
    )
    assert {j.code for j in out.projects[0].jobs} == {"JOB-MGT"}
    assert "EXP-MGT" in {x.code for x in out.projects[0].expertise}
    assert "EXP-SEC-BUILD" in {x.code for x in out.projects[0].expertise}


def test_sec_system_ops_supplements_job_ops_sys_with_pl_not_infra() -> None:
    """정보보안시스템 운영 + PL 운영 → JOB-OPS-SYS + PL; no EXP-INFRA."""
    page_texts = {
        (_DOC, 1): (
            "현대/기아자동차 정보보안시스템 운영 | 운영 | "
            "2012.07~2015.02 | PL 운영 | 현대오토에버"
        )
    }
    out = _run_supplement(
        [
            _project(
                name="현대/기아자동차 정보보안시스템 운영",
                customer="현대오토에버",
                start="2012.07",
                end="2015.02",
            )
        ],
        page_texts,
    )
    assert {j.code for j in out.projects[0].jobs} == {"JOB-MGT-PL", "JOB-OPS-SYS"}
    assert {x.code for x in out.projects[0].expertise} == {"EXP-SEC-OPS"}
    assert "EXP-INFRA" not in {x.code for x in out.projects[0].expertise}
    ops = next(j for j in out.projects[0].jobs if j.code == "JOB-OPS-SYS")
    assert ops.source_refs[0].quote_text == "정보보안시스템 운영"


def test_gangtaeryeon_style_ops_infra_mgt_ta_still_work() -> None:
    page_texts = {
        (_DOC, 1): (
            "2011년 제2차 자원통합 구축 HW1 사업(대전) | 개요 | "
            "2011.06.10~2011.12.24 | 시스템구축 및 운영 > IT시스템기술지원 | "
            "정부통합전산센터\n"
            "2013년 차기 복권통합구축사업 | 개요 | 2013.09.16~2013.12.31 | "
            "TA 사업관리 | 복권위원회"
        )
    }
    out = _run_supplement(
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
