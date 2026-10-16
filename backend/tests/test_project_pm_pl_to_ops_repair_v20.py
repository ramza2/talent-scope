"""v20 PROJECT JOB-MGT-PM/PL → JOB-OPS-SYS deterministic misclassification repair."""

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


def _run(projects, page_texts, allowed=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )
    from app.modules.analysis.compact_v8 import repair_project_pm_pl_to_ops_sys

    candidate = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="홍길동"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    return repair_project_pm_pl_to_ops_sys(
        candidate,
        page_texts=page_texts,
        allowed_documents=allowed
        or {_DOC: {page for (_d, page) in page_texts}},
    )


def _ops_row(
    name: str,
    customer: str,
    start_full: str,
    end_full: str,
    ops: str = "시스템구축 및 운영 > IT시스템기술지원",
) -> str:
    return f"{name} {start_full} ~ {end_full} {customer} {ops}"


def test_unsupported_pm_strong_ops_repairs() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ],
        page_texts,
    )
    assert n == 1
    job = out.projects[0].jobs[0]
    assert job.code == "JOB-OPS-SYS"
    assert job.raw_value == "JOB-MGT-PM"
    q = job.source_refs[0].quote_text or ""
    assert "시스템구축 및 운영" in q
    assert "IT시스템기술지원" in q


def test_unsupported_pl_strong_ops_repairs() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW2사업",
            "국가정보자원관리원",
            "2018.06.11",
            "2019.01.10",
            "시스템구축 및 운영 > IT시스템관리",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW2사업",
                customer="국가정보자원관리원",
                start="2018.06",
                end="2019.01",
                jobs=[("JOB-MGT-PL", "차장")],
            )
        ],
        page_texts,
    )
    assert n == 1
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"
    q = out.projects[0].jobs[0].source_refs[0].quote_text or ""
    assert "시스템구축 및 운영" in q


def test_literal_pm_strong_ops_preserves_pm() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "PM 사업관리")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "PM 사업관리"


def test_literal_pl_strong_ops_preserves_pl() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW2사업",
            "국가정보자원관리원",
            "2018.06.11",
            "2019.01.10",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW2사업",
                customer="국가정보자원관리원",
                start="2018.06",
                jobs=[("JOB-MGT-PL", "PL")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PL"


def test_pmo_quote_not_literal_pm_repairs_ops() -> None:
    """PMO is not a boundary-safe PM token; unsupported PM may repair."""
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "PMO")],
            )
        ],
        page_texts,
    )
    assert n == 1
    assert out.projects[0].jobs[0].code == "JOB-OPS-SYS"


def test_unsupported_pm_build_and_ops_alone_no_repair() -> None:
    page_texts = {
        (_DOC, 2): (
            "범정부 정보자원 통합구축 HW1사업 2019.07.01 ~ 2020.01.31 "
            "국가정보자원관리원 시스템구축 및 운영"
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"


def test_unsupported_pm_plain_ops_no_repair() -> None:
    page_texts = {
        (_DOC, 2): (
            "범정부 정보자원 통합구축 HW1사업 2019.07.01 ~ 2020.01.31 "
            "국가정보자원관리원 운영"
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "팀장")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"


def test_wrong_start_month_no_repair() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.08.01",
            "2020.01.31",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"


def test_ambiguous_same_customer_rows_no_repair() -> None:
    page_texts = {
        (_DOC, 2): "\n".join(
            [
                _ops_row(
                    "사업A",
                    "국가정보자원관리원",
                    "2019.07.01",
                    "2020.01.31",
                ),
                _ops_row(
                    "사업B",
                    "국가정보자원관리원",
                    "2019.07.15",
                    "2019.12.31",
                ),
            ]
        )
    }
    out, n = _run(
        [
            _project(
                name="다른이름",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"


def test_employment_only_no_repair() -> None:
    page_texts = {
        (_DOC, 2): (
            "근무경력 A회사 2019.07.01 ~ 2020.01.31 부장 "
            "시스템구축 및 운영 > IT시스템기술지원"
        )
    }
    out, n = _run(
        [
            _project(
                name="없는프로젝트",
                customer="A회사",
                start="2019.07",
                jobs=[("JOB-MGT-PM", "부장")],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert out.projects[0].jobs[0].code == "JOB-MGT-PM"


def test_job_mgt_ta_unknown_unchanged() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    out, n = _run(
        [
            _project(
                name="범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                jobs=[
                    ("JOB-MGT", "사업관리"),
                    ("JOB-ARC-TA", "TA"),
                    ("JOB-ETC-X", "기타"),
                ],
            )
        ],
        page_texts,
    )
    assert n == 0
    assert [j.code for j in out.projects[0].jobs] == [
        "JOB-MGT",
        "JOB-ARC-TA",
        "JOB-ETC-X",
    ]


def test_relation_count_unchanged() -> None:
    page_texts = {
        (_DOC, 2): _ops_row(
            "범정부 정보자원 통합구축 HW1사업",
            "국가정보자원관리원",
            "2019.07.01",
            "2020.01.31",
        )
    }
    projects = [
        _project(
            name="범정부 정보자원 통합구축 HW1사업",
            customer="국가정보자원관리원",
            start="2019.07",
            jobs=[("JOB-MGT-PM", "부장"), ("JOB-ARC-TA", "TA")],
        )
    ]
    before = sum(len(p.jobs) for p in projects)
    out, n = _run(projects, page_texts)
    after = sum(len(p.jobs) for p in out.projects)
    assert before == after == 2
    assert n == 1
    assert {j.code for j in out.projects[0].jobs} == {
        "JOB-OPS-SYS",
        "JOB-ARC-TA",
    }


def test_v19_flag_false_v20_true() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import resolve_profile_prompt

    v19_spec = resolve_profile_prompt("profile-extract-v19")
    v20_spec = resolve_profile_prompt("profile-extract-v20")
    assert v19_spec.repair_project_pm_pl_to_ops_sys is False
    assert not hasattr(v19, "REPAIR_PROJECT_PM_PL_TO_OPS_SYS")
    assert v20.REPAIR_PROJECT_PM_PL_TO_OPS_SYS is True
    assert v20_spec.repair_project_pm_pl_to_ops_sys is True


def test_synthetic_replay_0_to_8_real_candidates() -> None:
    """6 PM + 1 TA across [0]-[8]; report before→after + quote."""
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
            jobs=[("JOB-MGT-PM", "부장")],
        ),
        _project(
            name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            customer="국가정보자원관리원",
            start="2018.06",
            end="2019.01",
            jobs=[("JOB-MGT-PM", "차장")],
        ),
        _project(
            name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            customer="정부통합전산센터",
            start="2017.10",
            end="2018.03",
            jobs=[("JOB-MGT-PM", "팀장")],
        ),
        _project(
            name="2016년 HW자원통합구축 사업2-2(광주)",
            customer="정부통합전산센터",
            start="2016.08",
            end="2017.03",
            jobs=[("JOB-MGT-PM", "부장")],
        ),
        _project(
            name="국방통합정보관리소 정보체계 이전/통합 사업",
            customer="국방부",
            start="2014.02",
            end="2014.12",
            jobs=[("JOB-MGT-PM", "사업관리")],
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
            jobs=[("JOB-MGT-PM", "부장")],
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
    before_codes = [
        [j.code for j in p.jobs] for p in projects
    ]
    assert sum(len(c) for c in before_codes) == 7
    assert sum(1 for c in before_codes for x in c if x == "JOB-MGT-PM") == 6
    assert sum(1 for c in before_codes for x in c if x == "JOB-ARC-TA") == 1

    out, repaired = _run(projects, page_texts)
    assert sum(len(p.jobs) for p in out.projects) == 7
    assert repaired == 5

    lines = []
    for i, (proj_in, proj_out) in enumerate(zip(projects, out.projects)):
        if not proj_out.jobs:
            lines.append(f"[{i}] jobs=[]")
            continue
        for j_in, j_out in zip(proj_in.jobs, proj_out.jobs):
            q = None
            if j_out.source_refs:
                q = j_out.source_refs[0].quote_text
            lines.append(
                f"[{i}] before_code={j_in.code} after_code={j_out.code} quote={q!r}"
            )

    for i in range(5):
        assert out.projects[i].jobs[0].code == "JOB-OPS-SYS"
        q = out.projects[i].jobs[0].source_refs[0].quote_text or ""
        assert "시스템구축 및 운영" in q
    assert out.projects[5].jobs[0].code == "JOB-ARC-TA"
    assert out.projects[5].jobs[0].source_refs[0].quote_text == "TA"
    # [6] no strong OPS on fail-closed row → remain PM
    assert out.projects[6].jobs[0].code == "JOB-MGT-PM"

    artifact = "/opt/cursor/artifacts/project-pm-pl-to-ops-replay.txt"
    os.makedirs("/opt/cursor/artifacts", exist_ok=True)
    open(artifact, "w").write("\n".join(lines) + "\n")
    # Also print for agent final report
    print("\n".join(lines))
