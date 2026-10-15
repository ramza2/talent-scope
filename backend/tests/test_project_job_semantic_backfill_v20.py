"""v20 PROJECT JOB semantic evidence backfill from unique project rows."""

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
    end: str = "2013.12",
    jobs: list[tuple[str, str | None]] | None = None,
    root_quote: str | None = None,
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    job_rels = []
    for code, quote in (
        [("JOB-OPS-SYS", None)] if jobs is None else jobs
    ):
        refs = [_ref(quote)] if quote is not None else []
        job_rels.append(
            CodeRefCandidate(code=code, raw_value=code, source_refs=refs)
        )
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[_ref(root_quote or name)],
        jobs=job_rels,
    )


def _run(projects, page_texts, allowed=None, *, apply_strict: bool = False):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_semantic_project_job_evidence,
        drop_project_jobs_without_literal_pm_pl_evidence,
    )

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    out = backfill_semantic_project_job_evidence(
        candidate,
        page_texts=page_texts,
        allowed_documents=allowed
        or {_DOC: {page for (_d, page) in page_texts}},
    )
    if apply_strict:
        out = apply_normalized_quote_evidence(out)
        out = drop_project_jobs_without_literal_pm_pl_evidence(out)
    return out


def test_ops_sys_strong_semantic_row_backfill() -> None:
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
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    jobs = out.projects[0].jobs
    assert len(jobs) == 1
    assert jobs[0].code == "JOB-OPS-SYS"
    q = jobs[0].source_refs[0].quote_text or ""
    assert "시스템구축 및 운영" in q
    assert "IT시스템기술지원" in q


def test_ops_sys_build_operate_alone_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영"
        )
    }
    out = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []


def test_plain_unyeong_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 운영"
        )
    }
    out = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []


def test_job_mgt_saupgwanri_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "영화진흥위원회 전산실 유지보수 사업 2008.12.01 ~ 2010.04.30 "
            "영화진흥위원회 사업관리"
        )
    }
    out = _run(
        [
            _project(
                name="영화진흥위원회 전산실 유지보수 사업",
                customer="영화진흥위원회",
                start="2008.12",
                end="2010.04",
                jobs=[("JOB-MGT", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].code == "JOB-MGT"
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "사업관리"


def test_ta_literal_backfill_and_preserve() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 TA"
        )
    }
    filled = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                jobs=[("JOB-ARC-TA", None)],
            )
        ],
        page_texts,
    )
    assert filled.projects[0].jobs[0].source_refs[0].quote_text == "TA"

    preserved = _run(
        [
            _project(
                name="2013년 차기 복권통합구축사업",
                customer="기획재정부",
                start="2013.09",
                jobs=[("JOB-ARC-TA", "TA")],
            )
        ],
        {(_DOC, 2): "unrelated 2013.09.01 ~ 2013.12.31 기획재정부 xx"},
    )
    assert preserved.projects[0].jobs[0].source_refs[0].quote_text == "TA"


def test_pm_pl_literal_boundary_safe() -> None:
    page_texts = {
        (_DOC, 2): (
            "범정부 HW1사업 2019.07.01 ~ 2020.01.31 국가정보자원관리원 PM 사업관리\n"
            "별도 PL사업 2018.06.11 ~ 2019.01.10 국가정보자원관리원 PL"
        )
    }
    out = _run(
        [
            _project(
                name="범정부 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT-PM", None)],
            ),
            _project(
                name="별도 PL사업",
                customer="국가정보자원관리원",
                start="2018.06",
                end="2019.01",
                jobs=[("JOB-MGT-PL", None)],
            ),
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "PM"
    assert out.projects[1].jobs[0].source_refs[0].quote_text == "PL"


def test_pmo_must_not_satisfy_pm() -> None:
    page_texts = {
        (_DOC, 2): (
            "범정부 HW1사업 2019.07.01 ~ 2020.01.31 국가정보자원관리원 PMO 사업관리"
        )
    }
    out = _run(
        [
            _project(
                name="범정부 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT-PM", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []


def test_ws_customer_same_start_unique_fallback() -> None:
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
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    q = out.projects[0].jobs[0].source_refs[0].quote_text or ""
    assert "시스템구축 및 운영" in q
    assert "IT시스템기술지원" in q


def test_wrong_start_month_no_backfill() -> None:
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
                customer="정부통합전산센터",
                start="2011.07",
                end="2011.12",
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []


def test_ambiguous_rows_no_backfill() -> None:
    page_texts = {
        (_DOC, 2): (
            "A사업 2011.06.10 ~ 2011.11.15 정부통합전산센터 "
            "시스템구축 및 운영 > IT시스템기술지원\n"
            "B사업 2011.06.01 ~ 2011.10.31 정부통합전산센터 "
            "시스템구축 및 운영 > IT시스템관리"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"


def test_employment_only_row_no_backfill() -> None:
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
                jobs=[("JOB-OPS-SYS", None)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs == []


def test_existing_valid_ref_preserved() -> None:
    good = "시스템구축 및 운영 > IT시스템관리"
    page_texts = {
        (_DOC, 2): (
            "국방통합정보관리소 정보체계 이전/통합 사업 "
            "2014.02.07 ~ 2014.12.06 국방부 시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out = _run(
        [
            _project(
                name="국방통합정보관리소 정보체계 이전/통합 사업",
                customer="국방부",
                start="2014.02",
                end="2014.12",
                jobs=[("JOB-OPS-SYS", good)],
            )
        ],
        page_texts,
    )
    assert out.projects[0].jobs[0].source_refs[0].quote_text == good


def test_no_new_job_codes_count_unchanged_before_strict() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 "
            "시스템구축 및 운영 > IT시스템기술지원 TA"
        )
    }
    projects = [
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            jobs=[("JOB-OPS-SYS", None), ("JOB-ARC-TA", None), ("JOB-ETC-X", None)],
        )
    ]
    out = _run(projects, page_texts)
    codes = [j.code for j in out.projects[0].jobs]
    assert codes == ["JOB-OPS-SYS", "JOB-ARC-TA", "JOB-ETC-X"]
    assert out.projects[0].jobs[2].source_refs == []


def test_eommu_alone_does_not_create_or_keep_job_via_backfill() -> None:
    """업무이관/유지보수/업무분석 alone must not backfill JOB-OPS-SYS."""
    page_texts = {
        (_DOC, 2): (
            "2011년 HW사업 2011.06.10 ~ 2011.11.15 정부통합전산센터 업무이관\n"
            "영화사업 2008.12.01 ~ 2010.04.30 영화진흥위원회 유지보수\n"
            "BCP사업 2008.09.01 ~ 2008.12.31 행정안전부 업무분석"
        )
    }
    out = _run(
        [
            _project(
                name="2011년 HW사업",
                customer="정부통합전산센터",
                start="2011.06",
                jobs=[("JOB-OPS-SYS", None)],
            ),
            _project(
                name="영화사업",
                customer="영화진흥위원회",
                start="2008.12",
                end="2010.04",
                jobs=[("JOB-OPS-SYS", None)],
            ),
            _project(
                name="BCP사업",
                customer="행정안전부",
                start="2008.09",
                end="2008.12",
                jobs=[("JOB-OPS-SYS", None)],
            ),
        ],
        page_texts,
    )
    for proj in out.projects:
        assert proj.jobs[0].source_refs == []


def test_fresh_replay_jobs_0_to_8() -> None:
    """Synthetic replay on real fresh-run candidate projects (7 raw JOB)."""
    page_texts = {
        (_DOC, 2): "\n".join(
            [
                "2019년 제1차 범정부 정보자원 통합구축 HW1사업 "
                "2019.07.01 ~ 2020.01.31 국가정보자원관리원 "
                "시스템구축 및 운영 > IT시스템기술지원",
                "2018년 제1차 범정부 정보자원 통합구축 HW2 사업 "
                "2018.06.11 ~ 2019.01.10 국가정보자원관리원 "
                "시스템구축 및 운영 > IT시스템관리",
                "2017년 제2차 정보자원통합구축 HW1사업 (대전) "
                "2017.10.01 ~ 2018.03.31 정부통합전산센터 "
                "시스템구축 및 운영 > IT시스템기술지원",
                "2016년 HW자원통합구축 사업2-2(광주) "
                "2016.08.25 ~ 2017.03.25 정부통합전산센터 "
                "시스템구축 및 운영 > IT시스템관리",
                "국방통합정보관리소 정보체계 이전/통합 사업 "
                "2014.02.07 ~ 2014.12.06 국방부 "
                "시스템구축 및 운영 > IT시스템기술지원",
                "2013년 차기 복권통합구축사업 "
                "2013.09.16 ~ 2013.12.31 기획재정부 TA",
                "2011년 HW자원통합구축 사업2-1(대전) "
                "2011.06.10 ~ 2011.11.15 정부통합전산센터 업무이관",
                "영화진흥위원회 전산실 유지보수 사업 "
                "2008.12.01 ~ 2010.04.30 영화진흥위원회 유지보수",
                "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 "
                "2008.09.01 ~ 2008.12.31 행정안전부 업무분석",
            ]
        )
    }
    projects = [
        _project(
            name="2019년 제1차 범정부 정보자원 통합구축 HW1사업",
            customer="국가정보자원관리원",
            start="2019.07",
            end="2020.01",
            jobs=[("JOB-OPS-SYS", None)],
        ),
        _project(
            name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            customer="국가정보자원관리원",
            start="2018.06",
            end="2019.01",
            jobs=[("JOB-OPS-SYS", None)],
        ),
        _project(
            name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            customer="정부통합전산센터",
            start="2017.10",
            end="2018.03",
            jobs=[("JOB-OPS-SYS", None)],
        ),
        _project(
            name="2016년 HW자원통합구축 사업2-2(광주)",
            customer="정부통합전산센터",
            start="2016.08",
            end="2017.03",
            jobs=[("JOB-OPS-SYS", None)],
        ),
        _project(
            name="국방통합정보관리소 정보체계 이전/통합 사업",
            customer="국방부",
            start="2014.02",
            end="2014.12",
            jobs=[("JOB-OPS-SYS", None)],
        ),
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            end="2013.12",
            jobs=[("JOB-ARC-TA", "TA")],
        ),
        _project(
            name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
            customer="정부통합 전산센터",
            start="2011.06",
            end="2011.12",
            jobs=[("JOB-OPS-SYS", None)],
            root_quote="11.06 ~ \n12.01\n업무이관\n정부통합 전산센터",
        ),
        _project(
            name="영화진흥위원회 전산실 유지보수 사업",
            customer="영화진흥위원회",
            start="2008.12",
            end="2010.04",
            jobs=[],
            root_quote="08.12 ~ \n10.04\n유지보수\n영화진흥위원회",
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.09",
            end="2008.12",
            jobs=[],
            root_quote="08.09 ~ \n08.12\n업무분석\n행정안전부",
        ),
    ]
    # 7 raw JOB relations before backfill
    raw_job_count = sum(len(p.jobs) for p in projects)
    assert raw_job_count == 7

    out = _run(projects, page_texts)
    assert sum(len(p.jobs) for p in out.projects) == 7

    lines = []
    for i, proj in enumerate(out.projects):
        if not proj.jobs:
            lines.append(f"[{i}] jobs=[]")
            continue
        for job in proj.jobs:
            q = None
            if job.source_refs:
                q = job.source_refs[0].quote_text
            lines.append(f"[{i}] code={job.code} quote={q!r}")

    # OPS-SYS on [0]-[4] should have semantic quotes
    for i in range(5):
        q = out.projects[i].jobs[0].source_refs[0].quote_text or ""
        assert out.projects[i].jobs[0].code == "JOB-OPS-SYS"
        assert "시스템구축 및 운영" in q
    # [5] TA preserved
    assert out.projects[5].jobs[0].code == "JOB-ARC-TA"
    assert out.projects[5].jobs[0].source_refs[0].quote_text == "TA"
    # [6] 업무이관 alone → no backfill
    assert out.projects[6].jobs[0].source_refs == []

    after_strict = _run(projects, page_texts, apply_strict=True)
    strict_by_idx = {
        i: [j.code for j in p.jobs] for i, p in enumerate(after_strict.projects)
    }
    assert strict_by_idx[5] == ["JOB-ARC-TA"]
    assert sum(1 for codes in strict_by_idx.values() for c in codes if c == "JOB-OPS-SYS") >= 5
    assert strict_by_idx[6] == []

    open("/opt/cursor/artifacts/project-job-semantic-replay.txt", "w").write(
        "\n".join(lines) + "\n"
    )


def test_v19_flag_false() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.backfill_semantic_project_job_evidence is False
    assert not hasattr(v19, "BACKFILL_SEMANTIC_PROJECT_JOB_EVIDENCE")


def test_v20_flag_true() -> None:
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import current_profile_prompt

    assert v20.BACKFILL_SEMANTIC_PROJECT_JOB_EVIDENCE is True
    assert current_profile_prompt().backfill_semantic_project_job_evidence is True
