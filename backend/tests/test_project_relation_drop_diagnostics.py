"""Privacy-safe PROJECTS relation drop-stage diagnostics (#79)."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC1}
_CATALOG: dict[str, tuple[str, bool]] = {
    "JOB-ARC-TA": ("JOB", True),
    "JOB-MGT": ("JOB", True),
    "EXP-MGT": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "BIZ-PUBLIC": ("BIZ", True),
    "TECH-SEC-AD": ("TECH", True),
}


def _pipeline(raw: dict, *, page_text: str):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
        project_relation_diagnostic_counts,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        raw,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=_CATALOG,
    )
    page_texts = {(_DOC1, 1): page_text}
    allowed = {_DOC1: {1}}
    pre_strict = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents=allowed,
        page_texts=page_texts,
    )
    final = apply_normalized_quote_evidence(pre_strict)
    counts = project_relation_diagnostic_counts(
        raw=raw,
        expanded=expanded,
        pre_strict=pre_strict,
        candidate=final,
    )
    return counts, expanded, pre_strict, final


def test_drop_during_expand_when_rm_missing() -> None:
    """A: j/x present in raw but no usable rm → dropped at expand."""
    raw = {
        "pr": [
            {
                "n": "프로젝트-A",
                "cust": "고객",
                "j": ["JOB-ARC-TA", "JOB-MGT"],
                "x": ["EXP-MGT", "EXP-INFRA"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트-A"}],
            }
        ]
    }
    counts, expanded, pre_strict, final = _pipeline(
        raw, page_text="프로젝트-A 고객 TA 사업관리"
    )
    assert counts["raw_j"] == 2
    assert counts["raw_x"] == 2
    assert counts["raw_rm_j_projects"] == 0
    assert counts["raw_rm_x_projects"] == 0
    assert counts["raw_rm_j_refs"] == 0
    assert counts["raw_rm_x_refs"] == 0
    assert counts["expanded_jobs"] == 0
    assert counts["expanded_expertise"] == 0
    assert counts["expanded_business_domains"] == 1
    assert counts["dropped_during_expand_j"] == 2
    assert counts["dropped_during_expand_x"] == 2
    assert counts["dropped_during_normalize_j"] == 0
    assert counts["dropped_during_strict_filter_j"] == 0
    assert counts["norm_jobs"] == 0
    assert counts["norm_expertise"] == 0
    assert counts["norm_business_domains"] == 1
    assert final.projects[0].jobs == []
    assert "JOB-ARC-TA" not in str(counts)
    assert "프로젝트-A" not in str(counts)


def test_drop_during_strict_filter_when_quote_not_on_page() -> None:
    """B: rm present → expand keeps j/x; normalize clears bad q; strict drops."""
    raw = {
        "pr": [
            {
                "n": "프로젝트-B",
                "cust": "고객",
                "j": ["JOB-ARC-TA"],
                "x": ["EXP-MGT"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트-B"}],
                "rm": {
                    "j": [{"d": "D1", "p": 1, "q": "페이지에없는TA문구"}],
                    "x": [{"d": "D1", "p": 1, "q": "페이지에없는사업관리문구"}],
                },
            }
        ]
    }
    # Root quote present; relation quotes are NOT substrings of the page.
    counts, expanded, pre_strict, final = _pipeline(
        raw, page_text="프로젝트-B 고객 실제원문"
    )
    assert counts["raw_j"] == 1
    assert counts["raw_x"] == 1
    assert counts["raw_rm_j_projects"] == 1
    assert counts["raw_rm_x_projects"] == 1
    assert counts["raw_rm_j_refs"] == 1
    assert counts["raw_rm_x_refs"] == 1
    assert counts["expanded_jobs"] == 1
    assert counts["expanded_expertise"] == 1
    assert counts["dropped_during_expand_j"] == 0
    assert counts["dropped_during_expand_x"] == 0
    # Relations still present after normalize (quote cleared, entry kept).
    assert counts["pre_strict_jobs"] == 1
    assert counts["pre_strict_expertise"] == 1
    assert counts["dropped_during_normalize_j"] == 0
    assert counts["dropped_during_normalize_x"] == 0
    # Strict quote filter removes quote-less j/x.
    assert counts["dropped_during_strict_filter_j"] == 1
    assert counts["dropped_during_strict_filter_x"] == 1
    assert counts["norm_jobs"] == 0
    assert counts["norm_expertise"] == 0
    assert counts["norm_business_domains"] == 1
    assert pre_strict.projects[0].jobs[0].source_refs
    assert all(
        not (ref.quote_text or "").strip()
        for ref in pre_strict.projects[0].jobs[0].source_refs
    )
    assert "페이지에없는TA문구" not in str(counts)
    assert "프로젝트-B" not in str(counts)


def test_survive_all_stages_with_verbatim_rm() -> None:
    raw = {
        "pr": [
            {
                "n": "프로젝트-C",
                "cust": "고객",
                "j": ["JOB-ARC-TA", "JOB-MGT"],
                "x": ["EXP-MGT"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트-C"}],
                "rm": {
                    "j": [{"d": "D1", "p": 1, "q": "TA 사업관리"}],
                    "x": [{"d": "D1", "p": 1, "q": "사업관리"}],
                },
            }
        ]
    }
    counts, *_ = _pipeline(raw, page_text="프로젝트-C 고객 TA 사업관리")
    assert counts["expanded_jobs"] == 2
    assert counts["expanded_expertise"] == 1
    assert counts["pre_strict_jobs"] == 2
    assert counts["norm_jobs"] == 2
    assert counts["norm_expertise"] == 1
    assert counts["dropped_during_expand_j"] == 0
    assert counts["dropped_during_normalize_j"] == 0
    assert counts["dropped_during_strict_filter_j"] == 0
    assert counts["dropped_j"] == 0


def test_diagnostic_field_set_is_counts_only() -> None:
    from app.modules.analysis.compact_v8 import project_relation_diagnostic_counts
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument

    empty = ProfileCandidateDocument.model_validate(
        {"schema_version": "profile-candidate-v1", "projects": []}
    )
    counts = project_relation_diagnostic_counts(raw={"pr": []}, candidate=empty)
    expected = {
        "projects_raw",
        "raw_j",
        "raw_t",
        "raw_x",
        "raw_b",
        "raw_ct",
        "raw_rm_j_projects",
        "raw_rm_t_projects",
        "raw_rm_x_projects",
        "raw_rm_j_refs",
        "raw_rm_t_refs",
        "raw_rm_x_refs",
        "expanded_projects",
        "expanded_jobs",
        "expanded_skills",
        "expanded_expertise",
        "expanded_business_domains",
        "expanded_customer_types",
        "pre_strict_projects",
        "pre_strict_jobs",
        "pre_strict_skills",
        "pre_strict_expertise",
        "pre_strict_business_domains",
        "pre_strict_customer_types",
        "norm_projects",
        "norm_jobs",
        "norm_skills",
        "norm_expertise",
        "norm_business_domains",
        "norm_customer_types",
        "dropped_j",
        "dropped_t",
        "dropped_x",
        "dropped_b",
        "dropped_ct",
        "dropped_during_expand_j",
        "dropped_during_expand_t",
        "dropped_during_expand_x",
        "dropped_during_normalize_j",
        "dropped_during_normalize_t",
        "dropped_during_normalize_x",
        "dropped_during_strict_filter_j",
        "dropped_during_strict_filter_t",
        "dropped_during_strict_filter_x",
    }
    assert set(counts.keys()) == expected
    assert all(isinstance(v, int) for v in counts.values())
