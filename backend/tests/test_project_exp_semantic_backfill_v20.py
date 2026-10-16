"""v20 PROJECT EXP semantic evidence backfill from unique project source rows."""

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
    end: str = "2013.12",
    exp_quote: str = "업무분석",
    code: str = "EXP-INFRA",
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[_ref(name)],
        expertise=[
            CodeRefCandidate(
                code=code,
                raw_value=code,
                source_refs=[_ref(exp_quote)],
            )
        ],
    )


def _doc(projects, page_texts, allowed=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_project_expertise_evidence,
        drop_project_expertise_without_semantic_evidence,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    allowed_documents = allowed or {
        _DOC: {page for (_doc, page) in page_texts}
    }
    out = backfill_semantic_project_expertise_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents=allowed_documents,
    )
    return drop_project_expertise_without_semantic_evidence(out)


def test_exact_project_row_semantic_exp_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _doc(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    exp = out.projects[0].expertise
    assert len(exp) == 1
    assert exp[0].code == "EXP-INFRA"
    assert "시스템구축 및 운영" in (exp[0].source_refs[0].quote_text or "")
    assert "IT시스템기술지원" in (exp[0].source_refs[0].quote_text or "")


def test_ws_customer_variant_same_start_unique_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합 전산센터",
                start="2011.06",
                end="2011.12",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]
    assert "IT시스템기술지원" in out.projects[0].expertise[0].source_refs[0].quote_text


def test_wrong_start_month_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.07",
                end="2011.12",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    assert out.projects[0].expertise == []


def test_ambiguous_same_customer_start_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원\n"
            "2011년 별도 HW 사업 2011.06.01 ~ 2011.10.31 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템관리"
        )
    }
    out = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    assert out.projects[0].expertise == []


def test_row_without_semantic_phrase_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 업무분석"
        )
    }
    out = _doc(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    assert out.projects[0].expertise == []


def test_employment_only_line_no_page_level_grab() -> None:
    page_texts = {
        (_DOC, 1): (
            "정부통합전산센터 2011.06.10 ~ 2011.11.15 대리\n"
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                exp_quote="업무분석",
            )
        ],
        page_texts,
    )
    assert out.projects[0].expertise == []


def test_existing_valid_relation_refs_preserved() -> None:
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_project_expertise_evidence,
    )

    good = "시스템구축 및 운영 > IT시스템기술지원"
    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=[
            ProjectCandidate(
                project_name="2013년 차기 복권통합구축사업",
                customer_name="기획재정부",
                start_date="2013.09",
                end_date="2013.12",
                source_refs=[_ref("2013년 차기 복권통합구축사업")],
                expertise=[
                    CodeRefCandidate(
                        code="EXP-INFRA",
                        source_refs=[_ref(good, page_no=9)],
                    )
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템관리"
        )
    }
    out = backfill_semantic_project_expertise_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents={_DOC: {2, 9}},
    )
    ref = out.projects[0].expertise[0].source_refs[0]
    assert ref.quote_text == good
    assert ref.page_no == 9


def test_unknown_exp_untouched() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_project_expertise_evidence,
    )

    projects = [
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            exp_quote="업무분석",
            code="EXP-AI-RAG",
        )
    ]
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = backfill_semantic_project_expertise_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents={_DOC: {2}},
    )
    assert out.projects[0].expertise[0].code == "EXP-AI-RAG"
    assert out.projects[0].expertise[0].source_refs[0].quote_text == "업무분석"


def test_fresh_replay_projects_5_to_8() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템기술지원\n"
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원\n"
            "영화진흥위원회 정보화 사업 2010.01.01 ~ 2010.12.31 영화진흥위원회 사업관리\n"
            "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 2008.03.01 ~ 2008.09.30 "
            "정부통합전산센터 업무분석"
        )
    }
    projects = [
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            exp_quote="업무분석",
        ),
        _project(
            name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
            customer="정부통합 전산센터",
            start="2011.06",
            end="2011.12",
            exp_quote="업무분석",
        ),
        _project(
            name="영화진흥위원회 정보화 사업",
            customer="영화진흥위원회",
            start="2010.01",
            end="2010.12",
            exp_quote="사업관리",
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="정부통합전산센터",
            start="2008.03",
            end="2008.09",
            exp_quote="업무분석",
        ),
    ]
    # Force [7] to claim EXP-INFRA with weak quote (no INFRA on that row).
    projects[2].expertise[0] = projects[2].expertise[0].model_copy(
        update={
            "code": "EXP-INFRA",
            "raw_value": "EXP-INFRA",
            "source_refs": [_ref("사업관리")],
        }
    )
    out = _doc(projects, page_texts)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]
    assert [e.code for e in out.projects[1].expertise] == ["EXP-INFRA"]
    assert out.projects[2].expertise == []
    assert out.projects[3].expertise == []


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.backfill_semantic_project_exp_evidence is False
    assert not hasattr(v19, "BACKFILL_SEMANTIC_PROJECT_EXP_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.BACKFILL_SEMANTIC_PROJECT_EXP_EVIDENCE is True
    assert current_profile_prompt().backfill_semantic_project_exp_evidence is True
