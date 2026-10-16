"""v20 deterministic root TECH leaf supplement from source name/alias literals."""

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
        _code("TECH-INFRA", name="Infrastructure", parent=None),
        _code("TECH-SEC", name="Security", parent=None),
        _code("TECH-LANG", name="Language", parent=None),
        _code("TECH-SEC-AD", name="Active Directory", parent="TECH-SEC"),
        _code("TECH-SEC-NAC", name="NAC", parent="TECH-SEC"),
        _code("TECH-SEC-SEP", name="SEP", parent="TECH-SEC"),
        _code("JOB-OPS-SYS", name="시스템 운영", parent=None, code_type="JOB"),
    ]


def _aliases():
    return {
        "TECH-SEC-AD": ["AD"],
        "TECH-SEC-NAC": ["NAC"],
        "TECH-SEC-SEP": ["SEP"],
    }


def _empty_core(**kwargs):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강상원"),
        skills=kwargs.get("skills") or [],
        jobs=kwargs.get("jobs") or [],
        expertise=kwargs.get("expertise") or [],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _run(doc, *, page_texts, aliases=None, codes=None, allowed=None):
    from app.modules.analysis.compact_v8 import (
        backfill_literal_core_tech_from_source,
        drop_core_skills_without_quote_evidence,
        drop_generic_activity_core_skills,
    )

    allowed = allowed or {_DOC: {1}}
    out = backfill_literal_core_tech_from_source(
        doc,
        codes=codes if codes is not None else _catalog(),
        aliases=aliases if aliases is not None else _aliases(),
        page_texts=page_texts,
        allowed_documents=allowed,
    )
    out = drop_generic_activity_core_skills(out)
    out = drop_core_skills_without_quote_evidence(out)
    return out


def test_ad_nac_sep_literals_supplement_three_leaf_techs() -> None:
    page_texts = {(_DOC, 1): "보유기술: AD NAC SEP"}
    out = _run(_empty_core(), page_texts=page_texts)
    by_code = {s.code: s for s in out.skills}
    assert set(by_code) == {"TECH-SEC-AD", "TECH-SEC-NAC", "TECH-SEC-SEP"}
    assert by_code["TECH-SEC-AD"].raw_value == "AD"
    assert by_code["TECH-SEC-NAC"].raw_value == "NAC"
    assert by_code["TECH-SEC-SEP"].raw_value == "SEP"
    for skill in out.skills:
        assert len(skill.source_refs) == 1
        assert skill.source_refs[0].document_id == _DOC
        assert skill.source_refs[0].page_no == 1
        assert skill.source_refs[0].quote_text == skill.raw_value
        assert skill.last_used_year is None
        assert skill.experience_months is None


def test_tech_infra_parent_not_created() -> None:
    page_texts = {(_DOC, 1): "보유기술: AD NAC SEP Infrastructure INFRA"}
    out = _run(_empty_core(), page_texts=page_texts)
    assert not any(s.code == "TECH-INFRA" for s in out.skills)
    assert not any(s.code == "TECH-SEC" for s in out.skills)
    assert not any(s.code == "TECH-LANG" for s in out.skills)


def test_road_does_not_match_ad_token() -> None:
    page_texts = {(_DOC, 1): "ROAD map and loading"}
    out = _run(_empty_core(), page_texts=page_texts)
    assert out.skills == []


def test_existing_same_tech_skill_no_duplicate() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate, SourceRef

    existing = SkillCandidate(
        code="TECH-SEC-AD",
        raw_value="Active Directory",
        source_refs=[
            SourceRef(document_id=_DOC, page_no=1, quote_text="Active Directory")
        ],
    )
    page_texts = {(_DOC, 1): "Active Directory AD NAC SEP"}
    out = _run(_empty_core(skills=[existing]), page_texts=page_texts)
    ad_skills = [s for s in out.skills if s.code == "TECH-SEC-AD"]
    assert len(ad_skills) == 1
    assert ad_skills[0].raw_value == "Active Directory"
    assert ad_skills[0].source_refs[0].quote_text == "Active Directory"
    assert {s.code for s in out.skills} == {
        "TECH-SEC-AD",
        "TECH-SEC-NAC",
        "TECH-SEC-SEP",
    }


def test_existing_ad_empty_refs_repaired_from_literal() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate

    existing = SkillCandidate(
        code="TECH-SEC-AD",
        raw_value="AD",
        last_used_year=2020,
        experience_months=12,
        source_refs=[],
    )
    page_texts = {(_DOC, 1): "보유기술: AD"}
    out = _run(_empty_core(skills=[existing]), page_texts=page_texts)
    assert len(out.skills) == 1
    skill = out.skills[0]
    assert skill.code == "TECH-SEC-AD"
    assert skill.raw_value == "AD"
    assert skill.last_used_year == 2020
    assert skill.experience_months == 12
    assert len(skill.source_refs) == 1
    assert skill.source_refs[0].quote_text == "AD"
    assert skill.source_refs[0].document_id == _DOC


def test_existing_ad_nac_sep_empty_refs_repaired_no_duplicate() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate

    skills = [
        SkillCandidate(code="TECH-SEC-AD", raw_value="AD", source_refs=[]),
        SkillCandidate(code="TECH-SEC-NAC", raw_value="NAC", source_refs=[]),
        SkillCandidate(code="TECH-SEC-SEP", raw_value="SEP", source_refs=[]),
    ]
    page_texts = {(_DOC, 1): "보유기술: AD NAC SEP"}
    out = _run(_empty_core(skills=skills), page_texts=page_texts)
    assert len(out.skills) == 3
    by_code = {s.code: s for s in out.skills}
    assert set(by_code) == {"TECH-SEC-AD", "TECH-SEC-NAC", "TECH-SEC-SEP"}
    for code, quote in (
        ("TECH-SEC-AD", "AD"),
        ("TECH-SEC-NAC", "NAC"),
        ("TECH-SEC-SEP", "SEP"),
    ):
        skill = by_code[code]
        assert skill.raw_value == quote
        assert len(skill.source_refs) == 1
        assert skill.source_refs[0].quote_text == quote


def test_existing_valid_ref_unchanged() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate, SourceRef
    from app.modules.analysis.compact_v8 import backfill_literal_core_tech_from_source

    existing = SkillCandidate(
        code="TECH-SEC-AD",
        raw_value="Active Directory",
        source_refs=[
            SourceRef(
                document_id=_DOC, page_no=1, quote_text="KEEP-EXISTING-QUOTE"
            )
        ],
    )
    page_texts = {(_DOC, 1): "KEEP-EXISTING-QUOTE AD NAC SEP"}
    out = backfill_literal_core_tech_from_source(
        _empty_core(skills=[existing]),
        codes=_catalog(),
        aliases=_aliases(),
        page_texts=page_texts,
        allowed_documents={_DOC: {1}},
    )
    ad = next(s for s in out.skills if s.code == "TECH-SEC-AD")
    assert ad.raw_value == "Active Directory"
    assert ad.source_refs[0].quote_text == "KEEP-EXISTING-QUOTE"
    assert len([s for s in out.skills if s.code == "TECH-SEC-AD"]) == 1


def test_existing_ad_empty_refs_road_not_repaired_then_dropped() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate

    existing = SkillCandidate(
        code="TECH-SEC-AD", raw_value="AD", source_refs=[]
    )
    page_texts = {(_DOC, 1): "ROAD map and loading"}
    out = _run(_empty_core(skills=[existing]), page_texts=page_texts)
    assert out.skills == []


def test_existing_ad_empty_refs_ambiguous_alias_not_repaired() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate
    from app.modules.analysis.compact_v8 import (
        backfill_literal_core_tech_from_source,
        drop_core_skills_without_quote_evidence,
    )

    codes = _catalog() + [
        _code("TECH-SEC-OTHER", name="Other Sec", parent="TECH-SEC"),
    ]
    aliases = {
        "TECH-SEC-AD": ["AD"],
        "TECH-SEC-OTHER": ["AD"],
        "TECH-SEC-NAC": ["NAC"],
        "TECH-SEC-SEP": ["SEP"],
    }
    existing = SkillCandidate(
        code="TECH-SEC-AD", raw_value="AD", source_refs=[]
    )
    page_texts = {(_DOC, 1): "보유기술: AD"}
    mid = backfill_literal_core_tech_from_source(
        _empty_core(skills=[existing]),
        codes=codes,
        aliases=aliases,
        page_texts=page_texts,
        allowed_documents={_DOC: {1}},
    )
    assert len(mid.skills) == 1
    assert mid.skills[0].source_refs == []
    out = drop_core_skills_without_quote_evidence(mid)
    assert out.skills == []


def test_alias_ambiguity_fail_closed_skip() -> None:
    codes = _catalog() + [
        _code("TECH-SEC-OTHER", name="Other Sec", parent="TECH-SEC"),
    ]
    aliases = {
        "TECH-SEC-AD": ["AD"],
        "TECH-SEC-OTHER": ["AD"],
        "TECH-SEC-NAC": ["NAC"],
        "TECH-SEC-SEP": ["SEP"],
    }
    page_texts = {(_DOC, 1): "보유기술: AD NAC SEP"}
    out = _run(
        _empty_core(), page_texts=page_texts, codes=codes, aliases=aliases
    )
    codes_out = {s.code for s in out.skills}
    assert "TECH-SEC-AD" not in codes_out
    assert "TECH-SEC-OTHER" not in codes_out
    assert codes_out == {"TECH-SEC-NAC", "TECH-SEC-SEP"}


def test_parent_category_tech_not_auto_created() -> None:
    page_texts = {(_DOC, 1): "Security Language Infrastructure"}
    out = _run(_empty_core(), page_texts=page_texts)
    assert out.skills == []


def test_no_source_literal_no_creation() -> None:
    page_texts = {(_DOC, 1): "시스템 운영 및 유지보수"}
    out = _run(_empty_core(), page_texts=page_texts)
    assert out.skills == []


def test_v19_flag_false_v20_true() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    assert resolve_profile_prompt(
        "profile-extract-v19"
    ).backfill_literal_core_tech_from_source is False
    assert not hasattr(v19, "BACKFILL_LITERAL_CORE_TECH_FROM_SOURCE")
    assert v20.BACKFILL_LITERAL_CORE_TECH_FROM_SOURCE is True
    assert resolve_profile_prompt(
        "profile-extract-v20"
    ).backfill_literal_core_tech_from_source is True


def test_preserves_existing_llm_skills() -> None:
    from app.ai.schemas.profile_candidate import SkillCandidate, SourceRef

    oracle = SkillCandidate(
        code="TECH-DB-ORACLE",
        raw_value="Oracle",
        source_refs=[
            SourceRef(document_id=_DOC, page_no=1, quote_text="Oracle")
        ],
    )
    codes = _catalog() + [
        _code("TECH-DB-ORACLE", name="Oracle", parent="TECH-DB"),
    ]
    page_texts = {(_DOC, 1): "Oracle AD NAC SEP"}
    out = _run(
        _empty_core(skills=[oracle]), page_texts=page_texts, codes=codes
    )
    assert out.skills[0].code == "TECH-DB-ORACLE"
    assert out.skills[0].raw_value == "Oracle"
    assert {s.code for s in out.skills} == {
        "TECH-DB-ORACLE",
        "TECH-SEC-AD",
        "TECH-SEC-NAC",
        "TECH-SEC-SEP",
    }
