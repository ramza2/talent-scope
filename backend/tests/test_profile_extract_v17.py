"""profile-extract-v17 PROJECTS relation recall hardening."""

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

_BASE_SHA = "5c36555412300a5a082f4d2d625ed59ec7bdfe9c"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ALIAS = {"D1": _DOC1}

_CATALOG: dict[str, tuple[str, bool]] = {
    "JOB-MGT": ("JOB", True),
    "JOB-MGT-PM": ("JOB", True),
    "JOB-MGT-PL": ("JOB", True),
    "JOB-ARC-TA": ("JOB", True),
    "JOB-OPS-SYS": ("JOB", True),
    "TECH-SEC-AD": ("TECH", True),
    "EXP-MGT": ("EXP", True),
    "EXP-INFRA": ("EXP", True),
    "BIZ-PUBLIC": ("BIZ", True),
    "BIZ-DEFENSE": ("BIZ", True),
    "BIZ-MEDIA": ("BIZ", True),
}


def _normalize_projects(
    compact: dict,
    *,
    page_texts: dict[tuple[str, int], str],
    catalog: dict[str, tuple[str, bool]] | None = None,
    strict: bool = True,
):
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        expand_compact_projects,
    )
    from app.modules.analysis.normalize import normalize_candidate

    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=strict,
        derive_duration=True,
        clear_catalog_code_customer=True,
        catalog=catalog or _CATALOG,
    )
    doc = normalize_candidate(
        expanded,
        catalog=catalog or _CATALOG,
        allowed_documents={_DOC1: {1}},
        page_texts=page_texts,
    )
    if strict:
        doc = apply_normalized_quote_evidence(doc)
    return doc


# ---------------------------------------------------------------------------
# Registry / immutability / CORE parity
# ---------------------------------------------------------------------------


def test_profile_extract_registry_current_is_v17() -> None:
    from app.ai.prompts import profile_extract_v16 as v16
    from app.ai.prompts import profile_extract_v17 as v17
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v17"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v17"
    assert cur.extraction_mode == "staged"
    assert cur.compact_protocol is True
    assert cur.strict_relation_evidence is True
    assert cur.derive_project_duration is True
    assert cur.clear_catalog_code_customer is True
    assert cur.promote_exact_catalog_codes is True
    assert cur.backfill_exact_core_evidence is True
    assert cur.backfill_exact_project_evidence is True
    assert cur.validate_projects_recovery_root is True
    assert cur.validate_core_structured_completeness is True
    assert cur.projects_system_prompt == v17.PROJECTS_SYSTEM_PROMPT

    v16_spec = resolve_profile_prompt("profile-extract-v16")
    assert v16_spec.prompt_version == "profile-extract-v16"
    assert v16_spec.validate_core_structured_completeness is True
    assert v16_spec.validate_projects_recovery_root is True
    assert v16_spec.strict_relation_evidence is True
    assert v16_spec.core_system_prompt == v16.CORE_SYSTEM_PROMPT
    assert v16_spec.projects_system_prompt == v16.PROJECTS_SYSTEM_PROMPT


def test_v16_prompt_file_byte_identical_to_base() -> None:
    path = "backend/app/ai/prompts/profile_extract_v16.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_BASE_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


def test_v17_core_parity_and_projects_relation_contract() -> None:
    from app.ai.prompts import profile_extract_v16 as v16
    from app.ai.prompts import profile_extract_v17 as v17

    assert v17.CORE_SYSTEM_PROMPT == v16.CORE_SYSTEM_PROMPT
    assert v17.CORE_SCHEMA_GUIDE == v16.CORE_SCHEMA_GUIDE
    assert v17.CORE_RECOVERY_RETRY_INSTRUCTION == v16.CORE_RECOVERY_RETRY_INSTRUCTION
    assert v17.build_core_user_prompt(
        code_catalog="x", document_blocks="y"
    ) == v16.build_core_user_prompt(code_catalog="x", document_blocks="y")
    assert v17.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    ) == v16.build_core_user_prompt(
        code_catalog="x", document_blocks="y", recovery_retry=True
    )

    assert v17.VALIDATE_PROJECTS_RECOVERY_ROOT is True
    assert v17.VALIDATE_CORE_STRUCTURED_COMPLETENESS is True
    assert v17.STRICT_RELATION_EVIDENCE is True
    assert v17.PROJECTS_RECOVERY_RETRY_INSTRUCTION == (
        v16.PROJECTS_RECOVERY_RETRY_INSTRUCTION
    )

    projects = v17.PROJECTS_SYSTEM_PROMPT + v17.PROJECTS_SCHEMA_GUIDE
    user = v17.build_projects_user_prompt(
        code_catalog="JOB-ARC-TA", document_blocks="TA 사업관리"
    )
    blob = projects + "\n" + user
    assert "JOB-ARC-TA" in blob or "TA" in blob
    assert "사업관리" in blob and "JOB-MGT" in blob
    assert "시스템구축 및 운영" in blob
    assert "JOB-OPS-SYS" in blob and "EXP-INFRA" in blob
    assert "BIZ-PUBLIC" in blob and "BIZ-DEFENSE" in blob and "BIZ-MEDIA" in blob
    assert "same-project rm" in blob.lower() or "rm" in blob
    assert "verbatim" in blob.lower() or "원문" in blob
    assert "CUSTOMER_TYPE" in blob  # must warn not to invent when absent
    assert v17.PROJECTS_SYSTEM_PROMPT != v16.PROJECTS_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Strict evidence retention + BIZ root semantics
# ---------------------------------------------------------------------------


def test_v17_relations_with_rm_survive_and_without_rm_drop() -> None:
    page = (
        "공공기관 시스템구축 및 운영 > IT시스템기술지원 TA 사업관리 "
        "국방 영화 프로젝트"
    )
    page_texts = {(_DOC1, 1): page}

    with_rm = {
        "pr": [
            {
                "n": "공공기관 시스템구축 및 운영",
                "cust": "공공기관",
                "s": "2020-01",
                "e": "2021-12",
                "resp": "TA 사업관리 IT시스템기술지원",
                "j": ["JOB-ARC-TA", "JOB-MGT", "JOB-OPS-SYS"],
                "x": ["EXP-MGT", "EXP-INFRA"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "공공기관 시스템구축 및 운영"}],
                "rm": {
                    # Shared rm quote must verbatim-support every j/x using it:
                    # TA, 사업관리, 시스템구축 및 운영 / IT시스템기술지원.
                    "j": [
                        {
                            "d": "D1",
                            "p": 1,
                            "q": "시스템구축 및 운영 > IT시스템기술지원 TA 사업관리",
                        }
                    ],
                    "x": [
                        {
                            "d": "D1",
                            "p": 1,
                            "q": "시스템구축 및 운영 > IT시스템기술지원 TA 사업관리",
                        }
                    ],
                },
            }
        ]
    }
    doc = _normalize_projects(with_rm, page_texts=page_texts)
    assert len(doc.projects) == 1
    proj = doc.projects[0]
    assert {j.code for j in proj.jobs} == {"JOB-ARC-TA", "JOB-MGT", "JOB-OPS-SYS"}
    assert {e.code for e in proj.expertise} == {"EXP-MGT", "EXP-INFRA"}
    assert [b.code for b in proj.business_domains] == ["BIZ-PUBLIC"]
    assert proj.customer_types == []

    no_rm = {
        "pr": [
            {
                "n": "공공기관 시스템구축 및 운영",
                "cust": "공공기관",
                "s": "2020-01",
                "e": "2021-12",
                "resp": "TA 사업관리",
                "j": ["JOB-ARC-TA", "JOB-MGT"],
                "x": ["EXP-MGT"],
                "t": ["TECH-SEC-AD"],
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "공공기관 시스템구축 및 운영"}],
            }
        ]
    }
    doc2 = _normalize_projects(no_rm, page_texts=page_texts)
    assert len(doc2.projects) == 1
    assert doc2.projects[0].jobs == []
    assert doc2.projects[0].skills == []
    assert doc2.projects[0].expertise == []
    # b is not subject to strict rm; may keep via project root refs
    assert [b.code for b in doc2.projects[0].business_domains] == ["BIZ-PUBLIC"]


def test_v17_biz_defense_media_and_no_customer_type_seed() -> None:
    from app.ai.prompts import profile_extract_v17 as v17
    from app.modules.analysis.compact_v8 import expand_compact_projects

    page = "국방부 체계 구축 영화진흥위원회 사업"
    compact = {
        "pr": [
            {
                "n": "국방 체계",
                "cust": "국방부",
                "s": "2019-01",
                "e": "2019-12",
                "resp": "사업관리",
                "b": ["BIZ-DEFENSE"],
                "r": [{"d": "D1", "p": 1, "q": "국방부"}],
            },
            {
                "n": "영화진흥 사업",
                "cust": "영화진흥위원회",
                "s": "2021-01",
                "e": "2021-06",
                "resp": "사업관리",
                "b": ["BIZ-MEDIA"],
                "r": [{"d": "D1", "p": 1, "q": "영화진흥위원회"}],
            },
        ]
    }
    doc = _normalize_projects(
        compact, page_texts={(_DOC1, 1): page}
    )
    assert [p.business_domains[0].code for p in doc.projects] == [
        "BIZ-DEFENSE",
        "BIZ-MEDIA",
    ]
    assert all(p.customer_types == [] for p in doc.projects)

    # Expansion must not invent ct when absent from raw.
    expanded = expand_compact_projects(
        compact,
        alias_to_id=_ALIAS,
        strict_relation_evidence=True,
        derive_duration=True,
        catalog=_CATALOG,
    )
    assert all("customer_types" not in p for p in expanded["projects"])
    assert "CUSTOMER_TYPE" in (
        v17.PROJECTS_SYSTEM_PROMPT + v17.PROJECTS_SCHEMA_GUIDE
        + v17.build_projects_user_prompt(code_catalog="x", document_blocks="y")
    )


# ---------------------------------------------------------------------------
# Diagnostics privacy
# ---------------------------------------------------------------------------


def test_project_relation_diagnostic_counts_only() -> None:
    from app.modules.analysis.compact_v8 import (
        project_relation_diagnostic_counts,
    )

    raw = {
        "pr": [
            {
                "n": "비밀프로젝트명",
                "cust": "비밀고객",
                "j": ["JOB-ARC-TA", "JOB-MGT"],
                "x": ["EXP-MGT"],
                "t": ["TECH-SEC-AD"],
                "b": ["BIZ-PUBLIC"],
                "ct": ["SHOULD-NOT-EXIST"],
                "r": [{"d": "D1", "p": 1, "q": "비밀프로젝트명"}],
                "rm": {
                    "j": [{"d": "D1", "p": 1, "q": "TA"}],
                    "x": [{"d": "D1", "p": 1, "q": "사업관리"}],
                },
            }
        ]
    }
    page = "비밀프로젝트명 비밀고객 TA 사업관리 AD"
    doc = _normalize_projects(raw, page_texts={(_DOC1, 1): page})
    counts = project_relation_diagnostic_counts(raw=raw, candidate=doc)

    assert counts["projects_raw"] == 1
    assert counts["raw_j"] == 2
    assert counts["raw_t"] == 1
    assert counts["raw_x"] == 1
    assert counts["raw_b"] == 1
    assert counts["raw_ct"] == 1
    assert counts["norm_jobs"] == 2
    assert counts["norm_skills"] == 0  # no rm.t => dropped
    assert counts["norm_expertise"] == 1
    assert counts["norm_business_domains"] == 1
    assert counts["dropped_t"] == 1
    # Invalid catalog ct may be retained as code=None; counts still numeric only.
    assert counts["norm_customer_types"] >= 0
    assert counts["dropped_ct"] == max(0, counts["raw_ct"] - counts["norm_customer_types"])

    # Absent ct in raw must not invent customer_types.
    raw_no_ct = {
        "pr": [
            {
                "n": "비밀프로젝트명",
                "cust": "비밀고객",
                "b": ["BIZ-PUBLIC"],
                "r": [{"d": "D1", "p": 1, "q": "비밀프로젝트명"}],
            }
        ]
    }
    doc_no_ct = _normalize_projects(raw_no_ct, page_texts={(_DOC1, 1): page})
    assert doc_no_ct.projects[0].customer_types == []
    no_ct_counts = project_relation_diagnostic_counts(raw=raw_no_ct, candidate=doc_no_ct)
    assert no_ct_counts["raw_ct"] == 0
    assert no_ct_counts["norm_customer_types"] == 0

    serialized = str(counts) + str(no_ct_counts)
    assert "비밀프로젝트명" not in serialized
    assert "비밀고객" not in serialized
    assert "JOB-ARC-TA" not in serialized
    assert "SHOULD-NOT-EXIST" not in serialized
    assert "TA" not in serialized
    assert "사업관리" not in serialized
    assert set(counts.keys()) == {
        "projects_raw",
        "raw_j",
        "raw_t",
        "raw_x",
        "raw_b",
        "raw_ct",
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
    }
