"""v20 PROJECT JOB-MGT → JOB-OPS-SYS repair + JOB semantic guard."""

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
    for code, quote in jobs or []:
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


def _doc(projects):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _allowed(page_texts):
    return {_DOC: {page for (_d, page) in page_texts}}


def _repair(projects, page_texts):
    from app.modules.analysis.compact_v8 import repair_project_mgt_to_ops_sys

    return repair_project_mgt_to_ops_sys(
        _doc(projects),
        page_texts=page_texts,
        allowed_documents=_allowed(page_texts),
    )


def _full(projects, page_texts):
    """Repair → JOB backfill → strict → JOB semantic guard → PM/PL guard."""
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        backfill_semantic_project_job_evidence,
        drop_project_jobs_without_literal_pm_pl_evidence,
        drop_project_jobs_without_semantic_evidence,
        repair_project_mgt_to_ops_sys,
    )

    candidate = _doc(projects)
    allowed = _allowed(page_texts)
    out, n = repair_project_mgt_to_ops_sys(
        candidate, page_texts=page_texts, allowed_documents=allowed
    )
    out = backfill_semantic_project_job_evidence(
        out, page_texts=page_texts, allowed_documents=allowed
    )
    out = apply_normalized_quote_evidence(out)
    out = drop_project_jobs_without_semantic_evidence(out)
    out = drop_project_jobs_without_literal_pm_pl_evidence(out)
    return out, n


def _ops_row(
    name: str,
    customer: str,
    start_full: str,
    end_full: str,
    ops: str = "시스템구축 및 운영 > IT시스템기술지원",
) -> str:
    return f"{name} {start_full} ~ {end_full} {customer} {ops}"


def test_job_mgt_saupgwanri_preserve() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "영화진흥위원회 전산실 유지보수 사업",
            "영화진흥위원회",
            "2008.12.01",
            "2010.04.30",
            "사업관리",
        )
    }
    out, n = _repair(
        [
            _project(
                name="영화진흥위원회 전산실 유지보수 사업",
                customer="영화진흥위원회",
                start="2008.12",
                end="2010.04",
                jobs=[("JOB-MGT", "사업관리")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT"
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "사업관리"


def test_job_mgt_eommuigwan_strong_ops_repairs() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    out, n = _repair(
        [
            _project(
                name="2019년 제1차 범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT", "업무이관")],
            )
        ],
        page_texts,
    )
    assert n == 1
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"
    q = out.projects[0].jobs[0].source_refs[0].quote_text or ""
    assert "시스템구축 및 운영" in q
    assert "IT시스템기술지원" in q


def test_job_mgt_ap_igwan_strong_ops_repairs() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "국방통합정보관리소 정보체계 이전/통합 사업",
            "국방부",
            "2014.02.07",
            "2014.12.06",
            "시스템구축 및 운영 > IT시스템관리",
        )
    }
    out, n = _repair(
        [
            _project(
                name="국방통합정보관리소 정보체계 이전/통합 사업",
                customer="국방부",
                start="2014.02",
                end="2014.12",
                jobs=[("JOB-MGT", "AP 이관")],
            )
        ],
        page_texts,
    )
    assert n == 1
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"


def test_job_mgt_yujibosu_only_no_repair_then_guard_drop() -> None:
    page_texts = {
        (_DOC, 2): (
            "영화진흥위원회 전산실 유지보수 사업 2008.12.01 ~ 2010.04.30 "
            "영화진흥위원회 유지보수"
        )
    }
    projects = [
        _project(
            name="영화진흥위원회 전산실 유지보수 사업",
            customer="영화진흥위원회",
            start="2008.12",
            end="2010.04",
            jobs=[("JOB-MGT", "유지보수")],
        )
    ]
    repaired, n = _repair(projects, page_texts)
    assert n == 0
    assert repaired.projects[0].jobs[0].code == "JOB-MGT"
    out, _ = _full(projects, page_texts)
    assert out.projects[0].jobs == []


def test_job_mgt_eommbunseok_only_no_repair_then_guard_drop() -> None:
    page_texts = {
        (_DOC, 2): (
            "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업 "
            "2008.09.01 ~ 2008.12.31 행정안전부 업무분석"
        )
    }
    projects = [
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.09",
            end="2008.12",
            jobs=[("JOB-MGT", "업무분석")],
        )
    ]
    repaired, n = _repair(projects, page_texts)
    assert n == 0
    out, _ = _full(projects, page_texts)
    assert out.projects[0].jobs == []


def test_job_mgt_plain_unyeong_no_repair_drop() -> None:
    page_texts = {
        (_DOC, 2): (
            "2013년 차기 복권통합구축사업 2013.09.16 ~ 2013.12.31 기획재정부 운영"
        )
    }
    projects = [
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            jobs=[("JOB-MGT", "운영")],
        )
    ]
    repaired, n = _repair(projects, page_texts)
    assert n == 0
    out, _ = _full(projects, page_texts)
    assert out.projects[0].jobs == []


def test_wrong_start_month_no_repair() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "2011년 HW자원통합구축 사업2-1(대전)",
            "정부통합전산센터",
            "2011.06.10",
            "2011.11.15",
        )
    }
    out, n = _repair(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.07",
                end="2011.12",
                jobs=[("JOB-MGT", "업무이관")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT"


def test_ambiguous_source_rows_no_repair() -> None:
    page_texts = {
        (_DOC, 2): (
            "A사업 2011.06.10 ~ 2011.11.15 정부통합전산센터 "
            "시스템구축 및 운영 > IT시스템기술지원\n"
            "B사업 2011.06.01 ~ 2011.10.31 정부통합전산센터 "
            "시스템구축 및 운영 > IT시스템관리"
        )
    }
    out, n = _repair(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                jobs=[("JOB-MGT", "업무이관")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT"


def test_employment_only_row_no_repair() -> None:
    page_texts = {
        (_DOC, 1): (
            "정부통합전산센터 2011.06.10 ~ 2011.11.15 대리\n"
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out, n = _repair(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                jobs=[("JOB-MGT", "업무이관")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT"


def test_repaired_ops_sys_passes_semantic_guard() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            "국가정보자원관리원",
            "2018.06.11",
            "2019.01.10",
            "시스템구축 및 운영 > IT시스템관리",
        )
    }
    out, n = _full(
        [
            _project(
                name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
                customer="국가정보자원관리원",
                start="2018.06",
                end="2019.01",
                jobs=[("JOB-MGT", "업무이관")],
            )
        ],
        page_texts,
    )
    assert n == 1
    assert [j.code for j in out.projects[0].jobs] == ["JOB-OPS-SYS"]


def test_ops_sys_weak_evidence_drops() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_semantic_evidence,
    )

    out = drop_project_jobs_without_semantic_evidence(
        _doc(
            [
                _project(
                    name="P1",
                    customer="C1",
                    start="2019.07",
                    jobs=[("JOB-OPS-SYS", "시스템구축 및 운영")],
                )
            ]
        )
    )
    assert out.projects[0].jobs == []


def test_ta_without_literal_ta_drops() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_semantic_evidence,
    )

    out = drop_project_jobs_without_semantic_evidence(
        _doc(
            [
                _project(
                    name="P1",
                    customer="C1",
                    start="2013.09",
                    jobs=[("JOB-ARC-TA", "기술지원")],
                )
            ]
        )
    )
    assert out.projects[0].jobs == []


def test_pm_pl_behavior_unchanged_by_mgt_repair_and_job_guard() -> None:
    from app.modules.analysis.compact_v8 import (
        drop_project_jobs_without_literal_pm_pl_evidence,
        drop_project_jobs_without_semantic_evidence,
        repair_project_mgt_to_ops_sys,
    )

    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    # Literal PM preserved by MGT repair (not in scope) and JOB semantic guard.
    doc = _doc(
        [
            _project(
                name="범정부 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT-PM", "PM 사업관리")],
            )
        ]
    )
    repaired, n = repair_project_mgt_to_ops_sys(
        doc, page_texts=page_texts, allowed_documents=_allowed(page_texts)
    )
    assert n == 0
    guarded = drop_project_jobs_without_semantic_evidence(repaired)
    assert [j.code for j in guarded.projects[0].jobs] == ["JOB-MGT-PM"]
    final = drop_project_jobs_without_literal_pm_pl_evidence(guarded)
    assert [j.code for j in final.projects[0].jobs] == ["JOB-MGT-PM"]

    # Unsupported PM still left for PM/PL guard (MGT repair does not touch it).
    unsupported = _doc(
        [
            _project(
                name="범정부 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ]
    )
    repaired2, n2 = repair_project_mgt_to_ops_sys(
        unsupported,
        page_texts=page_texts,
        allowed_documents=_allowed(page_texts),
    )
    assert n2 == 0
    assert repaired2.projects[0].jobs[0].code == "JOB-MGT-PM"
    dropped = drop_project_jobs_without_literal_pm_pl_evidence(repaired2)
    assert dropped.projects[0].jobs == []


def test_relation_count_never_increases() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            "정부통합전산센터",
            "2017.10.01",
            "2018.03.31",
        )
    }
    projects = [
        _project(
            name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            customer="정부통합전산센터",
            start="2017.10",
            end="2018.03",
            jobs=[("JOB-MGT", "업무이관"), ("JOB-ARC-TA", "TA")],
        )
    ]
    before = sum(len(p.jobs) for p in projects)
    out, n = _repair(projects, page_texts)
    assert n == 1
    assert sum(len(p.jobs) for p in out.projects) == before


def test_fresh_replay_0_to_8_after_full_guard() -> None:
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
                "2017.10.01 ~ 2018.03.31 정부통합전산센터 사업관리",
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
            jobs=[("JOB-MGT", "업무이관")],
        ),
        _project(
            name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            customer="국가정보자원관리원",
            start="2018.06",
            end="2019.01",
            jobs=[("JOB-MGT", "업무이관")],
        ),
        _project(
            name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            customer="정부통합전산센터",
            start="2017.10",
            end="2018.03",
            jobs=[("JOB-MGT", "사업관리")],
        ),
        _project(
            name="2016년 HW자원통합구축 사업2-2(광주)",
            customer="정부통합전산센터",
            start="2016.08",
            end="2017.03",
            jobs=[("JOB-MGT", "업무이관")],
        ),
        _project(
            name="국방통합정보관리소 정보체계 이전/통합 사업",
            customer="국방부",
            start="2014.02",
            end="2014.12",
            jobs=[("JOB-MGT", "AP 이관")],
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
            jobs=[("JOB-MGT", "업무이관")],
            root_quote="11.06 ~ \n12.01\n업무이관\n정부통합 전산센터",
        ),
        _project(
            name="영화진흥위원회 전산실 유지보수 사업",
            customer="영화진흥위원회",
            start="2008.12",
            end="2010.04",
            jobs=[("JOB-MGT", "유지보수")],
            root_quote="08.12 ~ \n10.04\n유지보수\n영화진흥위원회",
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.09",
            end="2008.12",
            jobs=[("JOB-MGT", "업무분석")],
            root_quote="08.09 ~ \n08.12\n업무분석\n행정안전부",
        ),
    ]
    before_count = sum(len(p.jobs) for p in projects)
    assert before_count == 9
    out, repaired = _full(projects, page_texts)
    assert repaired == 4  # [0][1][3][4]; [2] has 사업관리; [6] no OPS row
    assert sum(len(p.jobs) for p in out.projects) <= before_count

    lines = []
    for i, proj in enumerate(out.projects):
        if not proj.jobs:
            lines.append(f"[{i}] jobs=[]")
            continue
        for job in proj.jobs:
            q = job.source_refs[0].quote_text if job.source_refs else None
            lines.append(f"[{i}] code={job.code} quote={q!r}")

    by_idx = {i: [j.code for j in p.jobs] for i, p in enumerate(out.projects)}
    assert by_idx[0] == ["JOB-OPS-SYS"]
    assert by_idx[1] == ["JOB-OPS-SYS"]
    assert by_idx[2] == ["JOB-MGT"]
    assert out.projects[2].jobs[0].source_refs[0].quote_text == "사업관리"
    assert by_idx[3] == ["JOB-OPS-SYS"]
    assert by_idx[4] == ["JOB-OPS-SYS"]
    assert by_idx[5] == ["JOB-ARC-TA"]
    assert by_idx[6] == []
    assert by_idx[7] == []
    assert by_idx[8] == []

    open("/opt/cursor/artifacts/project-mgt-to-ops-replay.txt", "w").write(
        "\n".join(lines) + "\n"
    )


def test_v19_flags_false_v20_true() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import (
        current_profile_prompt,
        resolve_profile_prompt,
    )

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    assert v19_spec.repair_project_mgt_to_ops_sys is False
    assert v19_spec.require_semantic_project_job_evidence is False
    assert not hasattr(v19, "REPAIR_PROJECT_MGT_TO_OPS_SYS")
    assert not hasattr(v19, "REQUIRE_SEMANTIC_PROJECT_JOB_EVIDENCE")
    assert v20.REPAIR_PROJECT_MGT_TO_OPS_SYS is True
    assert v20.REQUIRE_SEMANTIC_PROJECT_JOB_EVIDENCE is True
    cur = current_profile_prompt()
    assert cur.repair_project_mgt_to_ops_sys is True
    assert cur.require_semantic_project_job_evidence is True
