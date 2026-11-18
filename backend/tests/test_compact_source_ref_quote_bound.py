"""Regression: SourceRef quote_text must stay ≤500 and page-verifiable."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import SourceRef
from app.modules.analysis.compact_v8 import (
    SOURCE_REF_QUOTE_MAX_LEN,
    _expand_ref,
    bounded_verified_quote_text,
    expand_compact_projects,
    repair_whitespace_project_evidence,
)

_ALIAS = {"D1": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}
_DOC = _ALIAS["D1"]


def test_expand_ref_drops_overlong_unverified_quote() -> None:
    overlong = "가" * (SOURCE_REF_QUOTE_MAX_LEN + 1)
    out = _expand_ref({"d": "D1", "p": 1, "q": overlong}, _ALIAS)
    assert out is not None
    assert out["document_id"] == _DOC
    assert out["page_no"] == 1
    assert "quote_text" not in out


def test_expand_ref_keeps_quote_at_max_length() -> None:
    exact = "나" * SOURCE_REF_QUOTE_MAX_LEN
    out = _expand_ref({"d": "D1", "p": 2, "q": exact}, _ALIAS)
    assert out is not None
    assert out["quote_text"] == exact


def test_bounded_verified_quote_clips_to_page_substring() -> None:
    page = ("프로젝트 근거 " + ("다" * 600) + " 끝")
    quote = "다" * 600
    bounded = bounded_verified_quote_text(quote, page_text=page)
    assert bounded is not None
    assert len(bounded) == SOURCE_REF_QUOTE_MAX_LEN
    assert bounded in page
    # Pydantic SourceRef accepts the bounded quote.
    ref = SourceRef(document_id=_DOC, page_no=1, quote_text=bounded)
    assert ref.quote_text == bounded


def test_whitespace_repair_never_emits_overlong_quote() -> None:
    # Collapsed LLM quote; original page span is longer than 500 with spaces.
    body = " ".join(["라"] * 520)
    page = f"머리 {body} 꼬리"
    collapsed = "라" * 520
    expanded = {
        "projects": [
            {
                "project_name": "긴근거",
                "source_refs": [
                    {"document_id": _DOC, "page_no": 1, "quote_text": collapsed}
                ],
            }
        ]
    }
    repaired, counts = repair_whitespace_project_evidence(
        expanded, page_texts={(_DOC, 1): page}
    )
    assert counts["whitespace_repaired_root_refs"] >= 1
    quote = repaired["projects"][0]["source_refs"][0]["quote_text"]
    assert isinstance(quote, str)
    assert len(quote) <= SOURCE_REF_QUOTE_MAX_LEN
    assert quote in page
    # Must survive SourceRef validation used by normalize.
    SourceRef.model_validate(repaired["projects"][0]["source_refs"][0])


def test_expand_compact_projects_drops_overlong_root_quote() -> None:
    overlong = "마" * (SOURCE_REF_QUOTE_MAX_LEN + 50)
    compact = {
        "pr": [
            {
                "n": "샘플프로젝트",
                "r": [{"d": "D1", "p": 1, "q": overlong}],
            }
        ]
    }
    expanded = expand_compact_projects(compact, alias_to_id=_ALIAS)
    refs = expanded["projects"][0]["source_refs"]
    assert refs
    assert "quote_text" not in refs[0]
    SourceRef.model_validate(refs[0])


def test_semantic_core_exp_span_skips_overlong_unsupportive_quote() -> None:
    """Distant companions yield >500 span → skip enrich (no ValidationError)."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import (
        _semantic_core_exp_span_page_match,
        supplement_semantic_core_expertise_from_source,
    )

    filler = "패" * 520
    page = f"시스템구축 및 운영 {filler} IT시스템기술지원"
    # Extracted companion window exceeds 500 and clipped quote loses meaning.
    match = _semantic_core_exp_span_page_match(
        "EXP-INFRA",
        page_texts={(_DOC, 1): page},
        allowed_documents={_DOC: {1}},
    )
    assert match is None

    out = supplement_semantic_core_expertise_from_source(
        ProfileCandidateDocument(),
        page_texts={(_DOC, 1): page},
        allowed_documents={_DOC: {1}},
    )
    assert out.expertise == []


def test_semantic_core_exp_span_keeps_short_supportive_quote() -> None:
    from app.modules.analysis.compact_v8 import _semantic_core_exp_span_page_match

    page = "시스템구축 및 운영 > IT시스템기술지원"
    match = _semantic_core_exp_span_page_match(
        "EXP-INFRA",
        page_texts={(_DOC, 1): page},
        allowed_documents={_DOC: {1}},
    )
    assert match is not None
    assert match.quote_text is not None
    assert len(match.quote_text) <= SOURCE_REF_QUOTE_MAX_LEN
    assert match.quote_text in page
    SourceRef.model_validate(match.model_dump())
