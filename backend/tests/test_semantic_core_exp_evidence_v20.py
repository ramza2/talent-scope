"""v20 semantic evidence backfill for known root EXP codes."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _exp_doc(*, code: str, raw_value: str | None = None, source_refs=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ExpertiseCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SourceRef,
        SummaryCandidate,
    )

    refs = []
    if source_refs:
        refs = [
            SourceRef(document_id=r["document_id"], page_no=r["page_no"], quote_text=r["quote_text"])
            for r in source_refs
        ]
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        expertise=[
            ExpertiseCandidate(
                raw_value=raw_value or code,
                code=code,
                evidence_type="EXPLICIT",
                confidence=0.9,
                source_refs=refs,
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_infra_with_companion_backfills() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-INFRA")
    page_texts = {
        (_DOC_A, 1): "시스템구축 및 운영 > IT시스템기술지원 수행"
    }
    allowed = {_DOC_A: {1}}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents=allowed
    )
    exp = out.expertise[0]
    assert exp.code == "EXP-INFRA"
    assert exp.raw_value == "EXP-INFRA"
    assert exp.evidence_type == "EXPLICIT"
    assert exp.confidence == 0.9
    assert len(exp.source_refs) == 1
    assert exp.source_refs[0].quote_text == "시스템구축 및 운영"
    assert exp.source_refs[0].document_id == _DOC_A
    assert exp.source_refs[0].page_no == 1


def test_infra_alone_no_companion_no_backfill() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-INFRA")
    page_texts = {(_DOC_A, 1): "시스템구축 및 운영 담당"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {1}}
    )
    assert out.expertise[0].source_refs == []


def test_infra_info_system_ops_backfills() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-INFRA")
    page_texts = {(_DOC_A, 2): "정보시스템 운영 경력"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {2}}
    )
    assert out.expertise[0].source_refs[0].quote_text == "정보시스템 운영"


def test_mgt_saeopgwanri_backfills() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-MGT")
    page_texts = {(_DOC_B, 1): "주요역할: 사업관리"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_B: {1}}
    )
    assert out.expertise[0].source_refs[0].quote_text == "사업관리"
    assert out.expertise[0].source_refs[0].document_id == _DOC_B


def test_sec_ops_and_sec_build_mapping() -> None:
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ExpertiseCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        expertise=[
            ExpertiseCandidate(code="EXP-SEC-OPS", raw_value="EXP-SEC-OPS"),
            ExpertiseCandidate(code="EXP-SEC-BUILD", raw_value="EXP-SEC-BUILD"),
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    page_texts = {(_DOC_A, 1): "보안 운영 및 보안 구축 경험"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {1}}
    )
    assert out.expertise[0].source_refs[0].quote_text == "보안 운영"
    assert out.expertise[1].source_refs[0].quote_text == "보안 구축"


def test_existing_source_refs_unchanged() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(
        code="EXP-MGT",
        source_refs=[
            {
                "document_id": _DOC_A,
                "page_no": 9,
                "quote_text": "기존인용",
            }
        ],
    )
    page_texts = {(_DOC_A, 1): "사업관리"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {1, 9}}
    )
    assert len(out.expertise[0].source_refs) == 1
    assert out.expertise[0].source_refs[0].quote_text == "기존인용"
    assert out.expertise[0].source_refs[0].page_no == 9


def test_unknown_exp_code_unchanged() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-AI-RAG", raw_value="EXP-AI-RAG")
    page_texts = {(_DOC_A, 1): "사업관리 정보시스템 운영"}
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {1}}
    )
    assert out.expertise[0].source_refs == []
    assert out.expertise[0].code == "EXP-AI-RAG"


def test_disallowed_document_page_ignored() -> None:
    from app.modules.analysis.compact_v8 import (
        backfill_semantic_core_expertise_evidence,
    )

    doc = _exp_doc(code="EXP-MGT")
    page_texts = {
        (_DOC_A, 1): "사업관리",
        (_DOC_B, 1): "사업관리",
    }
    # DOC_A page 1 not allowed; DOC_B not in allowed at all.
    out = backfill_semantic_core_expertise_evidence(
        doc, page_texts=page_texts, allowed_documents={_DOC_A: {2}}
    )
    assert out.expertise[0].source_refs == []


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.backfill_semantic_core_exp_evidence is False
    assert not hasattr(v19, "BACKFILL_SEMANTIC_CORE_EXP_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.BACKFILL_SEMANTIC_CORE_EXP_EVIDENCE is True
    assert current_profile_prompt().backfill_semantic_core_exp_evidence is True
