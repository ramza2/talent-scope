"""profile-extract-v20 CORE generic-activity TECH drop."""

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

_BASE_SHA = "84d46de14f1ebcdcce437e45393e217567364f26"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_profile_extract_registry_current_is_v20() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import (
        CURRENT_PROFILE_PROMPT_VERSION,
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert CURRENT_PROFILE_PROMPT_VERSION == "profile-extract-v20"
    cur = current_profile_prompt()
    assert cur.prompt_version == "profile-extract-v20"
    assert cur.drop_generic_activity_core_tech is True
    assert v20.DROP_GENERIC_ACTIVITY_CORE_TECH is True
    assert cur.repair_whitespace_project_evidence is True
    assert cur.strict_relation_evidence is True

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.drop_generic_activity_core_tech is False
    assert not hasattr(v19, "DROP_GENERIC_ACTIVITY_CORE_TECH")
    assert v19_spec.projects_system_prompt == v19.PROJECTS_SYSTEM_PROMPT


def test_v19_prompt_file_byte_identical_to_base() -> None:
    path = "backend/app/ai/prompts/profile_extract_v19.py"
    current = _REPO_ROOT / path
    base = subprocess.check_output(
        ["git", "show", f"{_BASE_SHA}:{path}"],
        cwd=_REPO_ROOT,
    )
    assert hashlib.sha256(current.read_bytes()).digest() == hashlib.sha256(base).digest()


def test_v20_inherits_v19_flags_and_projects_contract() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20

    assert v20.REPAIR_WHITESPACE_PROJECT_EVIDENCE is True
    assert v20.STRICT_RELATION_EVIDENCE is True
    assert v20.PROJECTS_SCHEMA_GUIDE == v19.PROJECTS_SCHEMA_GUIDE
    assert "정보자원 통합구축" in v20.CORE_SYSTEM_PROMPT
    assert "s[].v must be the technology/product/tool phrase itself" in (
        v20.CORE_SYSTEM_PROMPT
    )


def _core_doc(*, skills, jobs=None, expertise=None, projects=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SkillCandidate,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        jobs=jobs
        or [JobCandidate(code="JOB-OPS-SYS", raw_value="시스템 운영")],
        skills=[SkillCandidate(**s) if isinstance(s, dict) else s for s in skills],
        expertise=expertise
        or [ExpertiseCandidate(code="EXP-INFRA", raw_value="정보시스템 운영")],
        projects=projects
        or [
            ProjectCandidate(
                project_name="P1",
                skills=[
                    CodeRefCandidate(code=None, raw_value="정보자원 통합구축")
                ],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_drop_generic_activity_phrases_from_root_skills_only() -> None:
    from app.modules.analysis.compact_v8 import drop_generic_activity_core_skills

    doc = _core_doc(
        skills=[
            {"raw_value": "정보자원 통합구축", "code": None},
            {"raw_value": "정보\u00a0자원  통합구축", "code": None},
            {"raw_value": "정보시스템 운영", "code": None},
            {"raw_value": "시스템구축 및 운영", "code": None},
            {"raw_value": "AD", "code": "TECH-SEC-AD"},
            {"raw_value": "SEP", "code": None},
            {"raw_value": "NAC", "code": None},
            {"raw_value": "Oracle", "code": "TECH-DB-ORACLE"},
            {"raw_value": "Oracle 구축", "code": "TECH-DB-ORACLE"},
            {"raw_value": "운영", "code": "TECH-FAKE"},
        ]
    )
    out = drop_generic_activity_core_skills(doc)
    kept = {(s.raw_value, s.code) for s in out.skills}
    assert ("정보자원 통합구축", None) not in kept
    assert ("정보\u00a0자원  통합구축", None) not in kept
    assert ("정보시스템 운영", None) not in kept
    assert ("시스템구축 및 운영", None) not in kept
    assert ("운영", "TECH-FAKE") not in kept
    assert ("AD", "TECH-SEC-AD") in kept
    assert ("SEP", None) in kept
    assert ("NAC", None) in kept
    assert ("Oracle", "TECH-DB-ORACLE") in kept
    assert ("Oracle 구축", "TECH-DB-ORACLE") in kept
    # JOB / EXP / project relation t[] unchanged.
    assert out.jobs[0].code == "JOB-OPS-SYS"
    assert out.expertise[0].code == "EXP-INFRA"
    assert out.projects[0].skills[0].raw_value == "정보자원 통합구축"


def test_v19_flag_false_leaves_generic_root_skill() -> None:
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.modules.analysis.compact_v8 import drop_generic_activity_core_skills

    v19 = resolve_profile_prompt("profile-extract-v19")
    assert v19.drop_generic_activity_core_tech is False
    doc = _core_doc(skills=[{"raw_value": "정보자원 통합구축", "code": None}])
    # Guard itself still drops when called; v19 runtime path must not call it.
    assert drop_generic_activity_core_skills(doc).skills == []


def test_v20_normalize_core_path_applies_guard() -> None:
    """Mirror compact CORE normalize: expand → normalize → optional drop."""
    from app.modules.analysis.compact_v8 import (
        drop_generic_activity_core_skills,
        expand_compact_core,
    )
    from app.modules.analysis.normalize import normalize_candidate

    compact = {
        "p": {"n": "홍길동"},
        "s": [
            {"v": "정보자원 통합구축"},
            {"v": "Oracle 구축", "c": "TECH-DB-ORACLE"},
            {"v": "AD", "c": "TECH-SEC-AD"},
        ],
        "j": [{"c": "JOB-OPS-SYS", "v": "시스템 운영"}],
        "x": [{"c": "EXP-INFRA", "v": "정보시스템 운영"}],
    }
    catalog = {
        "TECH-DB-ORACLE": ("TECH", True),
        "TECH-SEC-AD": ("TECH", True),
        "JOB-OPS-SYS": ("JOB", True),
        "EXP-INFRA": ("EXP", True),
    }
    expanded = expand_compact_core(compact, alias_to_id={})
    expanded["projects"] = []
    doc = normalize_candidate(expanded, catalog=catalog, allowed_documents={})
    # Without guard (v19 semantics) generic skill remains.
    assert any(s.raw_value == "정보자원 통합구축" for s in doc.skills)
    guarded = drop_generic_activity_core_skills(doc)
    assert not any(s.raw_value == "정보자원 통합구축" for s in guarded.skills)
    assert {s.code for s in guarded.skills} >= {"TECH-DB-ORACLE", "TECH-SEC-AD"}
    assert any(j.code == "JOB-OPS-SYS" for j in guarded.jobs)
    assert any(e.code == "EXP-INFRA" for e in guarded.expertise)
