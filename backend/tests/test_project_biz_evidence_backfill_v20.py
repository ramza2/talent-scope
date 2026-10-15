"""v20 PROJECT business_domains evidence backfill from unique project rows."""

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


def _ref(quote: str | None, *, page_no: int = 1):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=_DOC, page_no=page_no, quote_text=quote)


def _project(
    *,
    name: str,
    customer: str,
    start: str,
    end: str,
    biz_code: str = "BIZ-PUBLIC",
    biz_quote: str | None = "시스템구축 및 운영 > IT시스템기술지원",
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    refs = [_ref(biz_quote)] if biz_quote is not None else []
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[_ref(name)],
        business_domains=[
            CodeRefCandidate(code=biz_code, raw_value=biz_code, source_refs=refs)
        ],
    )


def _run(projects, page_texts, allowed=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import backfill_project_biz_evidence

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    return backfill_project_biz_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents=allowed
        or {_DOC: {page for (_d, page) in page_texts}},
    )


def test_exact_project_name_start_month_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                end="2013.12",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    biz = out.projects[0].business_domains
    assert len(biz) == 1
    assert biz[0].code == "BIZ-PUBLIC"
    q = biz[0].source_refs[0].quote_text or ""
    assert "2013년 차기 복권통합구축사업" in q
    assert "2013.09" in q
    assert "기획재정부" in q


def test_ws_customer_same_start_unique_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합 전산센터",
                start="2011.06",
                end="2011.12",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    q = out.projects[0].business_domains[0].source_refs[0].quote_text or ""
    assert "정부통합전산센터" in q
    assert "2011.06" in q
    assert out.projects[0].business_domains[0].code == "BIZ-PUBLIC"


def test_wrong_start_month_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 "
            "정부통합전산센터 시스템구축 및 운영"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.07",
                end="2011.12",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    assert out.projects[0].business_domains[0].source_refs == []


def test_ambiguous_rows_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "A사업 2011.06.10 ~ 2011.11.15 정부통합전산센터 역할1\n"
            "B사업 2011.06.01 ~ 2011.10.31 정부통합전산센터 역할2"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    assert out.projects[0].business_domains[0].source_refs == []
    assert out.projects[0].business_domains[0].code == "BIZ-PUBLIC"


def test_employment_only_line_no_backfill() -> None:
    page_texts = {
        (_DOC, 1): (
            "정부통합전산센터 2011.06.10 ~ 2011.11.15 대리\n"
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    assert out.projects[0].business_domains[0].source_refs == []
    assert out.projects[0].business_domains[0].code == "BIZ-PUBLIC"


def test_existing_valid_biz_ref_preserved() -> None:
    good = (
        "국방 메가센터 정보시스템 이전통합사업(육군) 2014.02.07 ~ 2014.12.06 "
        "국방부 시스템구축 및 운영"
    )
    page_texts = {(_DOC, 2): good + "\n다른사업 2014.02.01 ~ 2014.06.30 국방부 xx"}
    out = _run(
        [
            _project(
                name="국방 메가센터 정보시스템 이전통합사업(육군)",
                customer="국방부",
                start="2014.02",
                end="2014.12",
                biz_code="BIZ-DEFENSE",
                biz_quote=good,
            )
        ],
        page_texts,
    )
    ref = out.projects[0].business_domains[0].source_refs[0]
    assert ref.quote_text == good
    assert out.projects[0].business_domains[0].code == "BIZ-DEFENSE"


def test_biz_code_value_count_unchanged() -> None:
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    page_texts = {
        (_DOC, 2): (
            "영화진흥위원회 정보화 사업 2010.01.01 ~ 2010.12.31 영화진흥위원회 사업관리"
        )
    }
    proj = ProjectCandidate(
        project_name="영화진흥위원회 정보화 사업",
        customer_name="영화진흥위원회",
        start_date="2010.01",
        end_date="2010.12",
        source_refs=[_ref("영화진흥위원회 정보화 사업")],
        business_domains=[
            CodeRefCandidate(code="BIZ-MEDIA", raw_value="BIZ-MEDIA", source_refs=[]),
            CodeRefCandidate(code="BIZ-PUBLIC", raw_value="BIZ-PUBLIC", source_refs=[]),
        ],
    )
    out = _run([proj], page_texts)
    codes = [b.code for b in out.projects[0].business_domains]
    assert codes == ["BIZ-MEDIA", "BIZ-PUBLIC"]
    assert all(b.source_refs and b.source_refs[0].quote_text for b in out.projects[0].business_domains)


def test_unknown_biz_code_relation_kept() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 역할"
        )
    }
    out = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                end="2013.12",
                biz_code="BIZ-UNKNOWN-XYZ",
                biz_quote=None,
            )
        ],
        page_texts,
    )
    assert out.projects[0].business_domains[0].code == "BIZ-UNKNOWN-XYZ"
    assert out.projects[0].business_domains[0].source_refs[0].quote_text


def test_fresh_replay_biz_0_to_8() -> None:
    page_texts = {
        (_DOC, 2): "\n".join(
            [
                "정보자원통합구축 HW1사업 2018.06.11 ~ 2019.01.10 정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원",
                "정보자원통합구축 HW2사업 2016.08.25 ~ 2017.03.25 정부통합전산센터 시스템구축 및 운영 > IT시스템관리",
                "정보자원통합구축 HW3사업 2015.01.01 ~ 2015.06.30 정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원",
                "정보자원통합구축 HW4사업 2014.03.01 ~ 2014.09.30 정부통합전산센터 시스템구축 및 운영",
                "국방 메가센터 정보시스템 이전통합사업(육군) 2014.02.07 ~ 2014.12.06 국방부 시스템구축 및 운영",
                "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 시스템구축 및 운영 > IT시스템기술지원",
                "2011년 HW자원통합구축 사업2-1(대전) 2011.06.10 ~ 2011.11.15 정부통합전산센터 시스템구축 및 운영 > IT시스템기술지원",
                "영화진흥위원회 정보화 사업 2010.01.01 ~ 2010.12.31 영화진흥위원회 사업관리",
                "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 2008.03.01 ~ 2008.09.30 행정안전부 업무분석",
            ]
        )
    }
    projects = [
        _project(
            name="정보자원통합구축 HW1사업",
            customer="정부통합전산센터",
            start="2018.06",
            end="2019.01",
            biz_quote="시스템구축 및 운영 > IT시스템기술지원",
        ),
        _project(
            name="정보자원통합구축 HW2사업",
            customer="정부통합전산센터",
            start="2016.08",
            end="2017.03",
            biz_quote="시스템구축 및 운영 > IT시스템관리",
        ),
        _project(
            name="정보자원통합구축 HW3사업",
            customer="정부통합전산센터",
            start="2015.01",
            end="2015.06",
            biz_quote=None,
        ),
        _project(
            name="정보자원통합구축 HW4사업",
            customer="정부통합전산센터",
            start="2014.03",
            end="2014.09",
            biz_quote="근무경력 시스템구축 및 운영",
        ),
        _project(
            name="국방 메가센터 정보시스템 이전통합사업(육군)",
            customer="국방부",
            start="2014.02",
            end="2014.12",
            biz_code="BIZ-DEFENSE",
            biz_quote=None,
        ),
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            end="2013.12",
            biz_quote=None,
        ),
        _project(
            name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
            customer="정부통합 전산센터",
            start="2011.06",
            end="2011.12",
            biz_quote=None,
        ),
        _project(
            name="영화진흥위원회 정보화 사업",
            customer="영화진흥위원회",
            start="2010.01",
            end="2010.12",
            biz_code="BIZ-MEDIA",
            biz_quote=None,
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.03",
            end="2008.09",
            biz_quote=None,
        ),
    ]
    out = _run(projects, page_texts)
    expected_codes = [
        "BIZ-PUBLIC",
        "BIZ-PUBLIC",
        "BIZ-PUBLIC",
        "BIZ-PUBLIC",
        "BIZ-DEFENSE",
        "BIZ-PUBLIC",
        "BIZ-PUBLIC",
        "BIZ-MEDIA",
        "BIZ-PUBLIC",
    ]
    lines = []
    for i, (proj, code) in enumerate(zip(out.projects, expected_codes)):
        assert len(proj.business_domains) == 1
        assert proj.business_domains[0].code == code
        q = proj.business_domains[0].source_refs[0].quote_text
        assert q
        # Quote must include start month and customer (ws-fold) or project name.
        assert proj.start_date[:7].replace(".", "-")[:4] in q or proj.start_date[:7] in q
        lines.append(f"[{i}] {code} quote={q}")
    # Spot-check expected rows
    assert "국방" in out.projects[4].business_domains[0].source_refs[0].quote_text
    assert "복권" in out.projects[5].business_domains[0].source_refs[0].quote_text
    assert "정부통합전산센터" in out.projects[6].business_domains[0].source_refs[0].quote_text
    assert "영화진흥위원회" in out.projects[7].business_domains[0].source_refs[0].quote_text
    assert "행정안전부" in out.projects[8].business_domains[0].source_refs[0].quote_text
    # Wrong career quotes should be replaced
    assert "근무경력" not in (out.projects[3].business_domains[0].source_refs[0].quote_text or "")
    open("/opt/cursor/artifacts/project-biz-evidence-replay.txt", "w").write(
        "\n".join(lines) + "\n"
    )


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.backfill_project_biz_evidence is False
    assert not hasattr(v19, "BACKFILL_PROJECT_BIZ_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.BACKFILL_PROJECT_BIZ_EVIDENCE is True
    assert current_profile_prompt().backfill_project_biz_evidence is True
