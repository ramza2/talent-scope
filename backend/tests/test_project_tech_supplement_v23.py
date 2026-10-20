"""v23 fail-closed PROJECT TECH leaf supplement from same-project literals."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _code(
    code: str,
    *,
    name: str,
    parent: str | None,
    code_type: str = "TECH",
):
    return SimpleNamespace(
        code=code, name=name, parent_code=parent, code_type=code_type
    )


def _catalog():
    return [
        _code("TECH-SEC", name="Security", parent=None),
        _code("TECH-SEC-AD", name="Active Directory", parent="TECH-SEC"),
        _code("TECH-SEC-NAC", name="NAC", parent="TECH-SEC"),
        _code("TECH-SEC-SEP", name="SEP", parent="TECH-SEC"),
        _code("TECH-DB-ORACLE", name="Oracle", parent="TECH-DB"),
        _code("TECH-DB", name="Database", parent=None),
    ]


def _aliases():
    return {
        "TECH-SEC-AD": ["AD"],
        "TECH-SEC-NAC": ["NAC"],
        "TECH-SEC-SEP": ["SEP"],
        "TECH-DB-ORACLE": ["Oracle"],
    }


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
    skills=None,
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
        skills=_rels(skills),
    )


def _run(projects, page_texts, *, codes=None, aliases=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        supplement_literal_project_tech_from_source,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강상원"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    allowed = {_DOC: {page for (_d, page) in page_texts}}
    out = supplement_literal_project_tech_from_source(
        candidate,
        codes=codes if codes is not None else _catalog(),
        aliases=aliases if aliases is not None else _aliases(),
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    return apply_normalized_quote_evidence(out)


def test_flag_false_on_v22_true_on_v23() -> None:
    from app.ai.prompts import profile_extract_v22 as v22
    from app.ai.prompts import profile_extract_v23 as v23
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v23"
    assert v22.SUPPLEMENT_LITERAL_PROJECT_TECH_FROM_SOURCE is False
    assert resolve_profile_prompt(
        "profile-extract-v22"
    ).supplement_literal_project_tech_from_source is False
    assert v23.SUPPLEMENT_LITERAL_PROJECT_TECH_FROM_SOURCE is True
    assert resolve_profile_prompt(
        "profile-extract-v23"
    ).supplement_literal_project_tech_from_source is True


def test_gangsangwon_hyundai_sec_ops_supplements_ad_sep_nac() -> None:
    """현대/기아 정보보안 운영 detail lines → TECH-SEC-AD/SEP/NAC."""
    page_texts = {
        (_DOC, 2): (
            "현대/기아자동차 정보보안시스템 운영 | 운영 | 2012.07~2015.02 | "
            "PL 운영 | 현대오토에버\n"
            "보안솔루션 운영(AD,SEP,NAC 등)\n"
            "보안솔루션 기술지원 및 장애대응"
        )
    }
    projects = [
        _project(
            name="현대/기아자동차 정보보안시스템 운영",
            customer="현대오토에버",
            start="2012.07",
            end="2015.02",
            jobs=[("JOB-MGT-PL", "PL"), ("JOB-OPS-SYS", "정보보안시스템 운영")],
            expertise=[("EXP-SEC-OPS", "정보보안시스템 운영")],
        )
    ]
    out = _run(projects, page_texts)
    skills = {s.code: s for s in out.projects[0].skills}
    assert set(skills) == {"TECH-SEC-AD", "TECH-SEC-SEP", "TECH-SEC-NAC"}
    assert skills["TECH-SEC-AD"].source_refs[0].quote_text == "AD"
    assert skills["TECH-SEC-SEP"].source_refs[0].quote_text == "SEP"
    assert skills["TECH-SEC-NAC"].source_refs[0].quote_text == "NAC"
    # JOB/EXP/dates preserved.
    assert out.projects[0].start_date == "2012.07"
    assert out.projects[0].end_date == "2015.02"
    assert {j.code for j in out.projects[0].jobs} == {"JOB-MGT-PL", "JOB-OPS-SYS"}
    assert {x.code for x in out.projects[0].expertise} == {"EXP-SEC-OPS"}


def test_other_project_tech_not_bound() -> None:
    page_texts = {
        (_DOC, 1): (
            "A운영사업 | 개요 | 2017.01~2017.12 | 정보시스템1군 운영 | 고객A\n"
            "B보안사업 | 개요 | 2018.01~2018.12 | PL 운영 | 고객B\n"
            "보안솔루션 운영(AD,SEP,NAC 등)"
        )
    }
    out = _run(
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
    # Detail lines after B belong to B; A must stay empty.
    assert out.projects[0].skills == []
    assert {s.code for s in out.projects[1].skills} == {
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    }


def test_root_only_tech_literals_do_not_attach_to_project() -> None:
    page_texts = {
        (_DOC, 1): (
            "보유기술: AD SEP NAC\n"
            "현대/기아자동차 정보보안시스템 운영 | 운영 | 2012.07~2015.02 | "
            "PL 운영 | 현대오토에버"
        )
    }
    out = _run(
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
    # Literals sit above the project row; owned span+details have no AD/SEP/NAC.
    assert out.projects[0].skills == []


def test_existing_project_tech_preserved_no_duplicate() -> None:
    page_texts = {
        (_DOC, 1): (
            "현대/기아자동차 정보보안시스템 운영 | 운영 | 2012.07~2015.02 | "
            "PL 운영 | 현대오토에버\n"
            "보안솔루션 운영(AD,SEP,NAC 등)"
        )
    }
    existing_quote = "AD"
    out = _run(
        [
            _project(
                name="현대/기아자동차 정보보안시스템 운영",
                customer="현대오토에버",
                start="2012.07",
                end="2015.02",
                skills=[("TECH-SEC-AD", existing_quote)],
            )
        ],
        page_texts,
    )
    ad = [s for s in out.projects[0].skills if s.code == "TECH-SEC-AD"]
    assert len(ad) == 1
    assert ad[0].source_refs[0].quote_text == existing_quote
    assert {s.code for s in out.projects[0].skills} >= {
        "TECH-SEC-AD",
        "TECH-SEC-SEP",
        "TECH-SEC-NAC",
    }


def test_ambiguous_literal_skipped() -> None:
    # Two codes claim the same alias text → fail closed for that literal.
    codes = _catalog() + [
        _code("TECH-SEC-AD2", name="AD Alt", parent="TECH-SEC"),
    ]
    aliases = {
        **_aliases(),
        "TECH-SEC-AD2": ["AD"],
    }
    page_texts = {
        (_DOC, 1): (
            "현대/기아자동차 정보보안시스템 운영 | 운영 | 2012.07~2015.02 | "
            "PL 운영 | 현대오토에버\n"
            "보안솔루션 운영(AD,SEP,NAC 등)"
        )
    }
    out = _run(
        [
            _project(
                name="현대/기아자동차 정보보안시스템 운영",
                customer="현대오토에버",
                start="2012.07",
                end="2015.02",
            )
        ],
        page_texts,
        codes=codes,
        aliases=aliases,
    )
    codes_out = {s.code for s in out.projects[0].skills}
    assert "TECH-SEC-AD" not in codes_out
    assert "TECH-SEC-AD2" not in codes_out
    assert "TECH-SEC-SEP" in codes_out
    assert "TECH-SEC-NAC" in codes_out


def test_gangtaeryeon_project_job_exp_unchanged_by_tech_supplement() -> None:
    """Tech supplement must not alter JOB/EXP on 강태련-style rows."""
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
        drop_project_jobs_without_literal_pm_pl_evidence,
        drop_project_jobs_without_semantic_evidence,
        supplement_literal_project_tech_from_source,
        supplement_semantic_project_relations_from_source,
    )
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    page_texts = {
        (_DOC, 1): (
            "2011년 제2차 자원통합 구축 HW1 사업(대전) | 개요 | "
            "2011.06.10~2011.12.24 | 시스템구축 및 운영 > IT시스템기술지원 | "
            "정부통합전산센터\n"
            "2013년 차기 복권통합구축사업 | 개요 | 2013.09.16~2013.12.31 | "
            "TA 사업관리 | 복권위원회"
        )
    }
    projects = [
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
    ]
    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강태련"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    allowed = {_DOC: {1}}
    out = supplement_semantic_project_relations_from_source(
        candidate, page_texts=page_texts, allowed_documents=allowed
    )
    out = supplement_literal_project_tech_from_source(
        out,
        codes=_catalog(),
        aliases=_aliases(),
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    out = drop_project_jobs_without_semantic_evidence(
        out, enhanced_project_relation_semantics=True
    )
    out = drop_project_expertise_without_semantic_evidence(
        out, enhanced_project_relation_semantics=True
    )
    out = drop_project_jobs_without_literal_pm_pl_evidence(out)
    p2011 = out.projects[0]
    assert {j.code for j in p2011.jobs} == {"JOB-OPS-SYS"}
    assert {x.code for x in p2011.expertise} == {"EXP-INFRA"}
    assert p2011.skills == []
    p2013 = out.projects[1]
    assert {j.code for j in p2013.jobs} >= {"JOB-ARC-TA", "JOB-MGT"}
    assert "EXP-MGT" in {x.code for x in p2013.expertise}
    assert p2013.skills == []
