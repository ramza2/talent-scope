"""v20 PROJECT EXP semantic quote-evidence guard."""

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


def _proj_doc(*, expertise_quote: str, code: str = "EXP-INFRA", extra_refs=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ExpertiseCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )

    refs = [_ref(expertise_quote)]
    if extra_refs:
        refs.extend(_ref(q) for q in extra_refs)
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        expertise=[
            ExpertiseCandidate(
                code="EXP-INFRA",
                raw_value="EXP-INFRA",
                source_refs=[_ref("시스템 운영")],
            )
        ],
        projects=[
            ProjectCandidate(
                project_name="P1",
                customer_name="C1",
                responsibilities="업무분석",
                source_refs=[_ref("P1")],
                jobs=[
                    CodeRefCandidate(
                        code="JOB-OPS-SYS",
                        raw_value="시스템 운영",
                        source_refs=[_ref("시스템 운영")],
                    )
                ],
                skills=[
                    CodeRefCandidate(
                        code="TECH-DB-ORACLE",
                        raw_value="Oracle",
                        source_refs=[_ref("Oracle")],
                    )
                ],
                expertise=[
                    CodeRefCandidate(
                        code=code,
                        raw_value=code,
                        source_refs=refs,
                    )
                ],
                business_domains=[
                    CodeRefCandidate(code="BIZ-PUBLIC", raw_value="공공")
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_infra_it_support_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="시스템구축 및 운영 > IT시스템기술지원")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]


def test_infra_it_mgmt_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="시스템구축 및 운영 > IT시스템관리")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]


def test_infra_info_system_ops_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="정보시스템 운영")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]


def test_infra_system_ops_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="시스템 운영")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]


def test_infra_eopbunseok_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="업무분석")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert out.projects[0].expertise == []
    assert out.projects[0].project_name == "P1"
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"
    assert out.projects[0].skills[0].code == "TECH-DB-ORACLE"
    assert out.projects[0].business_domains[0].code == "BIZ-PUBLIC"
    assert out.expertise[0].code == "EXP-INFRA"


def test_infra_yujibosu_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="유지보수")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert out.projects[0].expertise == []


def test_infra_unyeong_alone_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="운영")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert out.projects[0].expertise == []


def test_infra_system_build_ops_alone_drop() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="시스템구축 및 운영")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert out.projects[0].expertise == []


def test_mgt_and_sec_mappings() -> None:
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=[
            ProjectCandidate(
                project_name="P",
                source_refs=[_ref("P")],
                expertise=[
                    CodeRefCandidate(
                        code="EXP-MGT", source_refs=[_ref("사업관리")]
                    ),
                    CodeRefCandidate(
                        code="EXP-SEC-OPS", source_refs=[_ref("보안 운영")]
                    ),
                    CodeRefCandidate(
                        code="EXP-SEC-BUILD", source_refs=[_ref("업무분석")]
                    ),
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert {e.code for e in out.projects[0].expertise} == {
        "EXP-MGT",
        "EXP-SEC-OPS",
    }


def test_multiple_refs_one_valid_keep() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(
        expertise_quote="업무분석",
        extra_refs=["시스템구축 및 운영 > IT시스템기술지원"],
    )
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-INFRA"]


def test_unknown_exp_unchanged() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    doc = _proj_doc(expertise_quote="업무분석", code="EXP-AI-RAG")
    out = drop_project_expertise_without_semantic_evidence(doc)
    assert [e.code for e in out.projects[0].expertise] == ["EXP-AI-RAG"]


def test_fresh_style_nine_projects_replay_exp_codes() -> None:
    """Offline replay of fresh analysis shape: projects[0..7] keep, [8] drops."""
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        drop_project_expertise_without_semantic_evidence,
    )

    good_q = "시스템구축 및 운영 > IT시스템기술지원"
    projects = []
    for i in range(8):
        projects.append(
            ProjectCandidate(
                project_name=f"P{i}",
                source_refs=[_ref(f"P{i}")],
                expertise=[
                    CodeRefCandidate(code="EXP-INFRA", source_refs=[_ref(good_q)])
                ],
            )
        )
    projects.append(
        ProjectCandidate(
            project_name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            responsibilities="업무분석",
            source_refs=[_ref("BCP")],
            expertise=[
                CodeRefCandidate(code="EXP-INFRA", source_refs=[_ref("업무분석")])
            ],
        )
    )
    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    out = drop_project_expertise_without_semantic_evidence(doc)
    codes = [[e.code for e in p.expertise] for p in out.projects]
    assert codes[:8] == [["EXP-INFRA"]] * 8
    assert codes[8] == []
    assert out.projects[8].project_name.startswith("2008년")


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.require_semantic_project_exp_evidence is False
    assert not hasattr(v19, "REQUIRE_SEMANTIC_PROJECT_EXP_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.REQUIRE_SEMANTIC_PROJECT_EXP_EVIDENCE is True
    assert current_profile_prompt().require_semantic_project_exp_evidence is True
