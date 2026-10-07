"""Fail-soft guards for compact source_ref page_no (p / page_no)."""

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
_ALIAS = {"D1": _DOC}


def test_expand_ref_valid_p_int_and_numeric_string() -> None:
    from app.modules.analysis.compact_v8 import _expand_ref

    as_int = _expand_ref({"d": "D1", "p": 1, "q": "프로젝트A"}, _ALIAS)
    assert as_int is not None
    assert as_int["page_no"] == 1
    assert as_int["quote_text"] == "프로젝트A"

    as_str = _expand_ref({"d": "D1", "p": "2", "q": "프로젝트B"}, _ALIAS)
    assert as_str is not None
    assert as_str["page_no"] == 2
    assert as_str["quote_text"] == "프로젝트B"


def test_expand_ref_malformed_p_omits_page_keeps_quote() -> None:
    from app.modules.analysis.compact_v8 import _expand_ref

    out = _expand_ref(
        {"d": "D1", "p": "경력 기술서", "q": "보안솔루션 운영"},
        _ALIAS,
    )
    assert out is not None
    assert "page_no" not in out
    assert out["document_id"] == _DOC
    assert out["quote_text"] == "보안솔루션 운영"


def test_expand_ref_malformed_canonical_page_no_omits_page() -> None:
    from app.modules.analysis.compact_v8 import _expand_ref

    out = _expand_ref(
        {
            "document_id": _DOC,
            "page_no": "경력 기술서",
            "quote_text": "보안솔루션 운영",
        },
        _ALIAS,
    )
    assert out is not None
    assert "page_no" not in out
    assert out["document_id"] == _DOC
    assert out["quote_text"] == "보안솔루션 운영"


def test_expand_ref_non_positive_and_non_int_page_fail_soft() -> None:
    from app.modules.analysis.compact_v8 import _expand_ref

    cases = [True, False, 1.5, 0, -1, [], {"p": 1}, None]
    for page in cases:
        out = _expand_ref({"d": "D1", "p": page, "q": "유지"}, _ALIAS)
        assert out is not None, page
        assert "page_no" not in out, page
        assert out["quote_text"] == "유지"


def test_project_compact_malformed_page_no_survives_normalize() -> None:
    """Production regression: free-text p must not fail ProfileCandidateDocument."""
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.compact_v8 import expand_compact_projects
    from app.modules.analysis.normalize import normalize_candidate

    page = "경력 기술서 보안솔루션 운영 프로젝트A"
    compact = {
        "pr": [
            {
                "n": "프로젝트A",
                "cust": "고객기관",
                "s": "2012.07",
                "e": "2015.02",
                "resp": "운영",
                "r": [{"d": "D1", "p": "경력 기술서", "q": "프로젝트A"}],
            }
        ]
    }
    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=False,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog={},
    )
    assert len(expanded["projects"]) == 1
    ref = expanded["projects"][0]["source_refs"][0]
    assert "page_no" not in ref
    assert ref["quote_text"] == "프로젝트A"

    doc = ProfileCandidateDocument.model_validate(expanded)
    assert len(doc.projects) == 1
    assert doc.projects[0].source_refs[0].page_no is None
    assert doc.projects[0].source_refs[0].quote_text == "프로젝트A"

    normalized = normalize_candidate(
        expanded,
        catalog={},
        allowed_documents={_DOC: {1}},
        page_texts={(_DOC, 1): page},
    )
    assert len(normalized.projects) == 1
    # Unknown page + quote may be cleared by normalize; must not raise.
    assert normalized.projects[0].project_name == "프로젝트A"
