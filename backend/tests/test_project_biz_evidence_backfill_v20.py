"""v20 PROJECT business_domains evidence backfill from unique project rows."""

from __future__ import annotations

import os
import re

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
    root_quote: str | None = None,
    source_refs=None,
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    refs = [_ref(biz_quote)] if biz_quote is not None else []
    if source_refs is not None:
        root_refs = source_refs
    elif root_quote is not None:
        root_refs = [_ref(root_quote)]
    else:
        root_refs = [_ref(name)]
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=root_refs,
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
        or (
            {_DOC: {page for (_d, page) in page_texts}}
            if page_texts
            else {_DOC: set()}
        ),
    )


def _quote_start_month(quote: str, *, project_name: str | None = None) -> str | None:
    """Best-effort start month token from a BIZ quote for replay assertions."""
    from app.modules.analysis.compact_v8 import parse_project_date_range_from_texts

    # Prefer explicit YYYY.MM / YYYY.MM.DD tokens (avoid YY.MM false hits inside
    # day-precision ranges like 2019.07.01 ~ 2020.01.31 → 07.01 ~ 2020.01).
    for m in re.finditer(
        r"(?<!\d)(\d{4})[./\-](\d{1,2})(?:[./\-]\d{1,2})?(?!\d)", quote
    ):
        month = int(m.group(2))
        if 1 <= month <= 12:
            return f"{int(m.group(1)):04d}.{month:02d}"
    parsed = parse_project_date_range_from_texts(
        [quote], project_name=project_name
    )
    if parsed is not None:
        return parsed[0]
    return None


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
        "국방통합정보관리소 정보체계 이전/통합 사업 2014.02.07 ~ 2014.12.06 "
        "국방부 시스템구축 및 운영"
    )
    page_texts = {(_DOC, 2): good + "\n다른사업 2014.02.01 ~ 2014.06.30 국방부 xx"}
    out = _run(
        [
            _project(
                name="국방통합정보관리소 정보체계 이전/통합 사업",
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
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 역할"
        )
    }
    proj = ProjectCandidate(
        project_name="2013년 차기 복권통합구축사업",
        customer_name="기획재정부",
        start_date="2013.09",
        end_date="2013.12",
        source_refs=[_ref("2013년 차기 복권통합구축사업")],
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


def test_multiline_root_ref_reuse_media_project() -> None:
    """[7] PPTX multi-line root with customer+YY.MM start → reuse as BIZ ref."""
    root = "08.12 ~ \n10.04\n유지보수\n영화진흥위원회"
    # Wrong-year KOSA-like row must not win over root reuse / start-month guard.
    page_texts = {
        (_DOC, 2): (
            "영화진흥위원회 정보화 사업 2010.01.01 ~ 2010.12.31 영화진흥위원회 사업관리"
        )
    }
    out = _run(
        [
            _project(
                name="영화진흥위원회 전산실 유지보수 사업",
                customer="영화진흥위원회",
                start="2008.12",
                end="2010.04",
                biz_code="BIZ-MEDIA",
                biz_quote=None,
                root_quote=root,
            )
        ],
        page_texts,
    )
    q = out.projects[0].business_domains[0].source_refs[0].quote_text
    assert q == root
    assert out.projects[0].business_domains[0].code == "BIZ-MEDIA"
    assert _quote_start_month(q) == "2008.12"


def test_multiline_root_ref_reuse_bcp_project() -> None:
    """[8] PPTX multi-line root 08.09 must not pick a 2008.03 KOSA row."""
    root = "08.09 ~ \n08.12\n업무분석\n행정안전부"
    page_texts = {
        (_DOC, 2): (
            "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 "
            "2008.03.01 ~ 2008.09.30 행정안전부 업무분석"
        )
    }
    out = _run(
        [
            _project(
                name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
                customer="행정안전부",
                start="2008.09",
                end="2008.12",
                biz_quote=None,
                root_quote=root,
            )
        ],
        page_texts,
    )
    q = out.projects[0].business_domains[0].source_refs[0].quote_text
    assert q == root
    assert "2008.03" not in q
    assert _quote_start_month(q) == "2008.09"


def test_root_ref_wrong_customer_not_reused() -> None:
    """[5] root customer mismatch → no reuse; KOSA unique row only."""
    root = "13.09 ~ \n13.12\n시스템구축\n베타시스템"  # not 기획재정부
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
                root_quote=root,
            )
        ],
        page_texts,
    )
    q = out.projects[0].business_domains[0].source_refs[0].quote_text or ""
    assert q != root
    assert "기획재정부" in q
    assert "2013.09" in q
    assert "베타시스템" not in q


def test_root_ref_wrong_start_month_not_reused() -> None:
    root = "11.07 ~ \n12.01\n업무이관\n정부통합 전산센터"
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합 전산센터",
                start="2011.06",
                end="2011.12",
                biz_quote=None,
                root_quote=root,
            )
        ],
        {},
    )
    assert out.projects[0].business_domains[0].source_refs == []


def test_fresh_replay_biz_0_to_8_real_candidates() -> None:
    """Replay using actual fresh-run candidate names/dates/customers."""
    root6 = "11.06 ~ \n12.01\n업무이관\n정부통합 전산센터"
    root7 = "08.12 ~ \n10.04\n유지보수\n영화진흥위원회"
    root8 = "08.09 ~ \n08.12\n업무분석\n행정안전부"
    # Include distractors with wrong start months that must never be chosen.
    page_texts = {
        (_DOC, 2): "\n".join(
            [
                "2019년 제1차 범정부 정보자원 통합구축 HW1사업 "
                "2019.07.01 ~ 2020.01.31 국가정보자원관리원 시스템구축 및 운영",
                "2018년 제1차 범정부 정보자원 통합구축 HW2 사업 "
                "2018.06.11 ~ 2019.01.10 국가정보자원관리원 시스템구축 및 운영",
                "2017년 제2차 정보자원통합구축 HW1사업 (대전) "
                "2017.10.01 ~ 2018.03.31 정부통합전산센터 시스템구축 및 운영",
                "2016년 HW자원통합구축 사업2-2(광주) "
                "2016.08.25 ~ 2017.03.25 정부통합전산센터 시스템구축 및 운영",
                "국방통합정보관리소 정보체계 이전/통합 사업 "
                "2014.02.07 ~ 2014.12.06 국방부 시스템구축 및 운영",
                "2013년 차기 복권통합구축사업 "
                "2013.09.16 ~ 2013.12.31 기획재정부 시스템구축 및 운영",
                # distractors (wrong start months for [0]/[1]/[7]/[8])
                "정보자원통합구축 HW1사업 2018.06.11 ~ 2019.01.10 "
                "정부통합전산센터 시스템구축 및 운영",
                "정보자원통합구축 HW2사업 2016.08.25 ~ 2017.03.25 "
                "정부통합전산센터 시스템구축 및 운영",
                "영화진흥위원회 정보화 사업 2010.01.01 ~ 2010.12.31 "
                "영화진흥위원회 사업관리",
                "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 "
                "2008.03.01 ~ 2008.09.30 행정안전부 업무분석",
            ]
        )
    }
    projects = [
        _project(
            name="2019년 제1차 범정부 정보자원 통합구축 HW1사업",
            customer="국가정보자원관리원",
            start="2019.07",
            end="2020.01",
            biz_quote="시스템구축 및 운영",
        ),
        _project(
            name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            customer="국가정보자원관리원",
            start="2018.06",
            end="2019.01",
            biz_quote="시스템구축 및 운영",
        ),
        _project(
            name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            customer="정부통합전산센터",
            start="2017.10",
            end="2018.03",
            biz_quote=None,
        ),
        _project(
            name="2016년 HW자원통합구축 사업2-2(광주)",
            customer="정부통합전산센터",
            start="2016.08",
            end="2017.03",
            biz_quote="근무경력 시스템구축 및 운영",
        ),
        _project(
            name="국방통합정보관리소 정보체계 이전/통합 사업",
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
            # mismatched root customer must not be reused
            root_quote="13.09 ~ \n13.12\n시스템구축\n베타시스템",
        ),
        _project(
            name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
            customer="정부통합 전산센터",
            start="2011.06",
            end="2011.12",
            biz_quote=None,
            root_quote=root6,
        ),
        _project(
            name="영화진흥위원회 전산실 유지보수 사업",
            customer="영화진흥위원회",
            start="2008.12",
            end="2010.04",
            biz_code="BIZ-MEDIA",
            biz_quote=None,
            root_quote=root7,
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.09",
            end="2008.12",
            biz_quote=None,
            root_quote=root8,
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
        q_start = _quote_start_month(q, project_name=proj.project_name)
        assert q_start == proj.start_date
        lines.append(
            f"[{i}] code={code} candidate_start={proj.start_date} "
            f"quote_start={q_start} quote={q!r}"
        )

    assert "2019.07" in (
        out.projects[0].business_domains[0].source_refs[0].quote_text or ""
    )
    assert "2018.06" in (
        out.projects[1].business_domains[0].source_refs[0].quote_text or ""
    )
    assert "국방통합정보관리소" in (
        out.projects[4].business_domains[0].source_refs[0].quote_text or ""
    )
    assert "기획재정부" in (
        out.projects[5].business_domains[0].source_refs[0].quote_text or ""
    )
    assert "베타시스템" not in (
        out.projects[5].business_domains[0].source_refs[0].quote_text or ""
    )
    assert out.projects[6].business_domains[0].source_refs[0].quote_text == root6
    assert out.projects[7].business_domains[0].source_refs[0].quote_text == root7
    assert out.projects[8].business_domains[0].source_refs[0].quote_text == root8
    assert "근무경력" not in (
        out.projects[3].business_domains[0].source_refs[0].quote_text or ""
    )
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
