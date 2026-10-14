"""profile-extract-v19 whitespace-safe PROJECTS evidence repair."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_BASE_SHA = "c587f656e918ec761a7b67fb97ea8d4ae7944560"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ALIAS = {"D1": _DOC1, "D2": _DOC2}
_CATALOG: dict[str, tuple[str, bool]] = {
    "JOB-ARC-TA": ("JOB", True),
    "JOB-MGT": ("JOB", True),
    "EXP-MGT": ("EXP", True),
    "BIZ-PUBLIC": ("BIZ", True),
}


def _pipeline(
    compact: dict,
    *,
    page_text: str,
    repair: bool = True,
    page_texts: dict[tuple[str, int], str] | None = None,
):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
        repair_whitespace_project_evidence,
    )
    from app.modules.analysis.normalize import normalize_candidate

    texts = page_texts if page_texts is not None else {(_DOC1, 1): page_text}
    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=_CATALOG,
    )
    counts = {
        "whitespace_repaired_root_refs": 0,
        "whitespace_repaired_j_refs": 0,
        "whitespace_repaired_t_refs": 0,
        "whitespace_repaired_x_refs": 0,
    }
    if repair:
        expanded, counts = repair_whitespace_project_evidence(
            expanded, page_texts=texts
        )
    doc = normalize_candidate(
        expanded,
        catalog=_CATALOG,
        allowed_documents={_DOC1: {1}, _DOC2: {1}},
        page_texts=texts,
    )
    doc = apply_normalized_quote_evidence(doc)
    return doc, expanded, counts


# ---------------------------------------------------------------------------
# Registry / immutability / prompt parity
# ---------------------------------------------------------------------------


def test_profile_extract_registry_v19_remains_resolvable() -> None:
    """v19 stays resolvable unchanged; current may advance (v20+)."""
    from app.ai.prompts import profile_extract_v18 as v18
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.prompt_version == "profile-extract-v19"
    assert v19_spec.repair_whitespace_project_evidence is True
    assert v19.REPAIR_WHITESPACE_PROJECT_EVIDENCE is True
    assert v19_spec.drop_generic_activity_core_tech is False
    assert v19_spec.require_core_tech_quote_evidence is False
    assert not hasattr(v19, "DROP_GENERIC_ACTIVITY_CORE_TECH")
    assert not hasattr(v19, "REQUIRE_CORE_TECH_QUOTE_EVIDENCE")
    assert v19_spec.strict_relation_evidence is True
    assert v19_spec.projects_system_prompt == v19.PROJECTS_SYSTEM_PROMPT

    v18_spec = resolve_profile_prompt("profile-extract-v18")
    assert v18_spec.repair_whitespace_project_evidence is False
    assert v18_spec.drop_generic_activity_core_tech is False
    assert v18_spec.require_core_tech_quote_evidence is False
    assert v18_spec.projects_system_prompt == v18.PROJECTS_SYSTEM_PROMPT
    assert not hasattr(v18, "REPAIR_WHITESPACE_PROJECT_EVIDENCE")


def test_v18_prompt_file_byte_identical_to_base() -> None:
    path = "backend/app/ai/prompts/profile_extract_v18.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_BASE_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


def test_v19_prompt_parity_with_v18() -> None:
    from app.ai.prompts import profile_extract_v18 as v18
    from app.ai.prompts import profile_extract_v19 as v19

    assert v19.CORE_SYSTEM_PROMPT == v18.CORE_SYSTEM_PROMPT
    assert v19.PROJECTS_SYSTEM_PROMPT == v18.PROJECTS_SYSTEM_PROMPT
    assert v19.CORE_SCHEMA_GUIDE == v18.CORE_SCHEMA_GUIDE
    assert v19.PROJECTS_SCHEMA_GUIDE == v18.PROJECTS_SCHEMA_GUIDE
    assert v19.build_core_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v18.build_core_user_prompt(code_catalog="x", document_blocks="y")
    assert v19.build_projects_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    ) == v18.build_projects_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )


# ---------------------------------------------------------------------------
# Repair behavior
# ---------------------------------------------------------------------------


def test_exact_quote_unchanged() -> None:
    page = "2013년 차기 복권통합구축사업 TA 사업관리"
    compact = {
        "pr": [
            {
                "n": "복권통합구축사업",
                "j": [
                    {
                        "c": "JOB-ARC-TA",
                        "r": [{"d": "D1", "p": 1, "q": "TA"}],
                    }
                ],
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "2013년 차기 복권통합구축사업",
                    }
                ],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_root_refs"] == 0
    assert counts["whitespace_repaired_j_refs"] == 0
    assert expanded["projects"][0]["source_refs"][0]["quote_text"] == (
        "2013년 차기 복권통합구축사업"
    )
    assert len(doc.projects) == 1
    assert doc.projects[0].jobs[0].code == "JOB-ARC-TA"


def test_space_and_nbsp_mismatch_repairs_to_original_span() -> None:
    # Source has ordinary spaces + NBSP that LLM quote collapses.
    page = "2013 년\u00a0차기 복권통합구축사업 TA 사업관리"
    original = "2013 년\u00a0차기 복권통합구축사업"
    compact = {
        "pr": [
            {
                "n": "복권통합",
                "j": [
                    {
                        "c": "JOB-ARC-TA",
                        "r": [{"d": "D1", "p": 1, "q": "TA"}],
                    }
                ],
                "x": [
                    {
                        "c": "EXP-MGT",
                        "r": [{"d": "D1", "p": 1, "q": "사업관리"}],
                    }
                ],
                "b": ["BIZ-PUBLIC"],
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "2013년 차기 복권통합구축사업",  # collapsed spaces/NBSP
                    }
                ],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_root_refs"] == 1
    assert expanded["projects"][0]["source_refs"][0]["quote_text"] == original
    assert len(doc.projects) == 1
    assert doc.projects[0].source_refs[0].quote_text == original
    assert {j.code for j in doc.projects[0].jobs} == {"JOB-ARC-TA"}
    assert {e.code for e in doc.projects[0].expertise} == {"EXP-MGT"}


def test_repaired_inline_jx_survive_normalize_and_strict() -> None:
    page = "시스템 운영\u00a0TA  사업관리 프로젝트명"
    compact = {
        "pr": [
            {
                "n": "프로젝트명",
                "j": [
                    {
                        "c": "JOB-ARC-TA",
                        "r": [{"d": "D1", "p": 1, "q": "TA"}],
                    },
                    {
                        "c": "JOB-MGT",
                        # collapsed double space / NBSP vs page
                        "r": [{"d": "D1", "p": 1, "q": "TA 사업관리"}],
                    },
                ],
                "x": [
                    {
                        "c": "EXP-MGT",
                        "r": [{"d": "D1", "p": 1, "q": "사업관리"}],
                    }
                ],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트명"}],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_j_refs"] >= 1
    assert len(doc.projects) == 1
    assert {j.code for j in doc.projects[0].jobs} == {"JOB-ARC-TA", "JOB-MGT"}
    assert {e.code for e in doc.projects[0].expertise} == {"EXP-MGT"}
    # Repaired quote must be exact substring of page.
    for job in doc.projects[0].jobs:
        for ref in job.source_refs:
            assert ref.quote_text in page


def test_non_whitespace_mismatch_not_repaired() -> None:
    page = "2013년 차기 복권통합구축사업"
    compact = {
        "pr": [
            {
                "n": "복권",
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "2013년 차기 복권통합고도화사업",  # different chars
                    }
                ],
                "j": [
                    {
                        "c": "JOB-ARC-TA",
                        "r": [{"d": "D1", "p": 1, "q": "없는문구"}],
                    }
                ],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_root_refs"] == 0
    assert counts["whitespace_repaired_j_refs"] == 0
    # Root quote cleared by normalize → project dropped by strict filter.
    assert doc.projects == []


def test_duplicate_ws_insensitive_matches_not_repaired() -> None:
    page = "TA 사업관리 구간1 TA 사업관리 구간2 프로젝트명"
    compact = {
        "pr": [
            {
                "n": "프로젝트명",
                "j": [
                    {
                        "c": "JOB-MGT",
                        # Collapsed form matches TWO page spans ignoring whitespace.
                        "r": [{"d": "D1", "p": 1, "q": "TA사업관리"}],
                    }
                ],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트명"}],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_j_refs"] == 0
    assert doc.projects[0].jobs == []  # quote cleared → strict drop


def test_wrong_document_or_page_not_repaired() -> None:
    page_texts = {
        (_DOC1, 1): "프로젝트명 다른내용",
        (_DOC2, 1): "2013 년 차기 복권통합구축사업",
    }
    compact = {
        "pr": [
            {
                "n": "프로젝트명",
                # Quote would match DOC2 page, but ref points at DOC1.
                "r": [
                    {
                        "d": "D1",
                        "p": 1,
                        "q": "2013년 차기 복권통합구축사업",
                    }
                ],
            }
        ]
    }
    doc, expanded, counts = _pipeline(
        compact, page_text="", page_texts=page_texts
    )
    assert counts["whitespace_repaired_root_refs"] == 0
    assert doc.projects == []


def test_jx_never_inherit_project_root_evidence() -> None:
    page = "프로젝트명만있는페이지"
    compact = {
        "pr": [
            {
                "n": "프로젝트명만있는페이지",
                "j": [{"c": "JOB-ARC-TA"}],  # no inline r / rm
                "x": [{"c": "EXP-MGT"}],
                "r": [{"d": "D1", "p": 1, "q": "프로젝트명만있는페이지"}],
            }
        ]
    }
    doc, expanded, counts = _pipeline(compact, page_text=page)
    assert counts["whitespace_repaired_j_refs"] == 0
    assert counts["whitespace_repaired_x_refs"] == 0
    assert "jobs" not in expanded["projects"][0]
    assert "expertise" not in expanded["projects"][0]
    assert len(doc.projects) == 1
    assert doc.projects[0].jobs == []
    assert doc.projects[0].expertise == []


def test_diagnostic_counts_expose_no_values() -> None:
    from app.modules.analysis.compact_v8 import (
        find_unique_whitespace_insensitive_span,
        repair_whitespace_project_evidence,
    )

    page = "2013 년 차기"
    assert (
        find_unique_whitespace_insensitive_span(page, "2013년차기")
        == "2013 년 차기"
    )
    expanded = {
        "schema_version": "profile-candidate-v1",
        "projects": [
            {
                "project_name": "비밀이름",
                "source_refs": [
                    {
                        "document_id": _DOC1,
                        "page_no": 1,
                        "quote_text": "2013년차기",
                    }
                ],
            }
        ],
    }
    _, counts = repair_whitespace_project_evidence(
        expanded, page_texts={(_DOC1, 1): page}
    )
    assert counts["whitespace_repaired_root_refs"] == 1
    serialized = str(counts)
    assert "비밀이름" not in serialized
    assert "2013" not in serialized
    assert set(counts.keys()) == {
        "whitespace_repaired_root_refs",
        "whitespace_repaired_j_refs",
        "whitespace_repaired_t_refs",
        "whitespace_repaired_x_refs",
    }
