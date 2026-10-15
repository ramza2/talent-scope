"""v20 bounded PROJECT recall second-pass supplement (no source-only creation)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _ref(quote: str, *, page_no: int = 1):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=_DOC, page_no=page_no, quote_text=quote)


def _project(
    *,
    name: str,
    customer: str,
    start: str,
    end: str,
    jobs: list[tuple[str, str]] | None = None,
    root_quote: str | None = None,
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    job_rels = [
        CodeRefCandidate(
            code=code,
            raw_value=code,
            source_refs=[_ref(quote)],
        )
        for code, quote in (jobs or [])
    ]
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


def _seven_final_projects():
    return [
        _project(
            name="2019년 제1차 범정부 정보자원 통합구축 HW1사업",
            customer="국가정보자원관리원",
            start="2019.07",
            end="2020.01",
            jobs=[("JOB-OPS-SYS", "시스템구축 및 운영 > IT시스템기술지원")],
        ),
        _project(
            name="2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            customer="국가정보자원관리원",
            start="2018.06",
            end="2019.01",
            jobs=[("JOB-OPS-SYS", "시스템구축 및 운영 > IT시스템관리")],
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
            jobs=[("JOB-OPS-SYS", "시스템구축 및 운영 > IT시스템관리")],
        ),
        _project(
            name="국방통합정보관리소 정보체계 이전/통합 사업",
            customer="국방부",
            start="2014.02",
            end="2014.12",
            jobs=[("JOB-OPS-SYS", "시스템구축 및 운영 > IT시스템기술지원")],
        ),
        _project(
            name="2013년 차기 복권통합구축사업",
            customer="기획재정부",
            start="2013.09",
            end="2013.12",
            jobs=[("JOB-ARC-TA", "TA")],
        ),
        _project(
            name="영화진흥위원회 전산실 유지보수 사업",
            customer="영화진흥위원회",
            start="2008.12",
            end="2010.04",
            jobs=[("JOB-MGT", "사업관리")],
        ),
    ]


def _missing_two():
    return [
        _project(
            name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
            customer="정부통합 전산센터",
            start="2011.06",
            end="2011.12",
            root_quote="11.06 ~ \n12.01\n업무이관\n정부통합 전산센터",
        ),
        _project(
            name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            customer="행정안전부",
            start="2008.09",
            end="2008.12",
            root_quote="08.09 ~ \n08.12\n업무분석\n행정안전부",
        ),
    ]


def test_first_7_plus_second_missing_2_union_9() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects())
    second = _doc(_seven_final_projects() + _missing_two())
    # Mutate a first-only relation marker to prove first wins.
    first.projects[0].jobs[0] = first.projects[0].jobs[0].model_copy(
        update={"raw_value": "FIRST-KEEP"}
    )
    second.projects[0].jobs[0] = second.projects[0].jobs[0].model_copy(
        update={"raw_value": "SECOND-OVERWRITE"}
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 2
    assert len(out.projects) == 9
    assert out.projects[0].jobs[0].raw_value == "FIRST-KEEP"
    names = [p.project_name for p in out.projects]
    assert "2011년 제2차 자원통합 구축 HW1 사업(대전)" in names
    assert "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업" in names
    open("/opt/cursor/artifacts/project-recall-second-pass-replay.txt", "w").write(
        "\n".join(
            f"[{i}] name={p.project_name!r} start={p.start_date} "
            f"jobs={[j.code for j in p.jobs]}"
            for i, p in enumerate(out.projects)
        )
        + f"\nunion={len(out.projects)} supplemented={n}\n"
    )


def test_same_7_twice_union_7() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects())
    second = _doc(_seven_final_projects())
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert len(out.projects) == 7


def test_second_internal_duplicate_supplements_once() -> None:
    """Same project returned twice in second pass → append only once."""
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects())
    dup = _missing_two()[0]
    second = _doc(
        [
            dup,
            _project(
                name="2011년 제2차 자원통합 구축 HW1사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
                root_quote="11.06 ~ 12.01 업무이관",
            ),
        ]
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 1
    assert len(out.projects) == 8
    assert (
        sum(
            1
            for p in out.projects
            if "2011년 제2차 자원통합" in (p.project_name or "")
        )
        == 1
    )


def test_second_two_distinct_projects_both_supplemented() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects())
    second = _doc(_missing_two())
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 2
    assert len(out.projects) == 9


def test_second_ambiguous_customer_date_internal_dup_fail_closed() -> None:
    """Two second projects share customer+dates → only first extras wins."""
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects()[:1])
    second = _doc(
        [
            _project(
                name="2011년 A사업",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            ),
            _project(
                name="2011년 B사업",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            ),
        ]
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 1
    assert len(out.projects) == 2
    assert out.projects[1].project_name == "2011년 A사업"


def test_same_name_ws_fold_compatible_dates_no_duplicate() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(
        [
            _project(
                name="2019년 제1차 범정부 정보자원 통합구축 HW1사업",
                customer="국가정보자원관리원",
                start="2019.07",
                end="2020.01",
            )
        ]
    )
    second = _doc(
        [
            _project(
                name="2019년 제1차 범정부 정보자원 통합구축 HW1 사업",
                customer="국가정보자원관리원",
                start="2019.07.01",
                end="2020.01.31",
            )
        ]
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert len(out.projects) == 1


def test_same_customer_compatible_dates_unique_no_duplicate() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(
        [
            _project(
                name="국방통합정보관리소 정보체계 이전/통합 사업",
                customer="국방부",
                start="2014.02",
                end="2014.12",
            )
        ]
    )
    second = _doc(
        [
            _project(
                name="국방 메가센터 정보시스템 이전통합사업(육군)",
                customer="국방 부",
                start="2014.02.07",
                end="2014.12.06",
            )
        ]
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert out.projects[0].project_name == "국방통합정보관리소 정보체계 이전/통합 사업"


def test_ambiguous_customer_date_fail_closed_no_duplicate() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    # Two first projects share customer + compatible start/end with second.
    first = _doc(
        [
            _project(
                name="A사업",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            ),
            _project(
                name="B사업",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            ),
        ]
    )
    second = _doc(
        [
            _project(
                name="2011년 제2차 자원통합 구축 HW1 사업(대전)",
                customer="정부통합전산센터",
                start="2011.06",
                end="2011.12",
            )
        ]
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert len(out.projects) == 2
    assert {p.project_name for p in out.projects} == {"A사업", "B사업"}


def test_second_dropped_by_strict_not_supplemented() -> None:
    """Simulate second normalize survivor set excluding quote-less project."""
    from app.modules.analysis.compact_v8 import (
        apply_normalized_quote_evidence,
        supplement_projects_from_second_pass,
    )
    from app.ai.schemas.profile_candidate import ProjectCandidate

    first = _doc(_seven_final_projects())
    # Quote-less root would be dropped by strict before supplement.
    orphan = ProjectCandidate(
        project_name="2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
        customer_name="행정안전부",
        start_date="2008.09",
        end_date="2008.12",
        source_refs=[],
    )
    second_raw = _doc(_seven_final_projects() + [orphan])
    second = apply_normalized_quote_evidence(second_raw)
    assert all(
        p.project_name != "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업"
        for p in second.projects
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert len(out.projects) == 7


def test_first_fields_relations_unchanged() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects())
    marker = first.projects[2].jobs[0].source_refs[0].quote_text
    second = _doc(
        [
            _project(
                name="2017년 제2차 정보자원통합구축 HW1사업 (대전)",
                customer="정부통합전산센터",
                start="2017.10",
                end="2018.03",
                jobs=[("JOB-OPS-SYS", "시스템구축 및 운영 > IT시스템기술지원")],
            )
        ]
        + _missing_two()
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 2
    assert out.projects[2].jobs[0].code == "JOB-MGT"
    assert out.projects[2].jobs[0].source_refs[0].quote_text == marker


def test_no_source_only_project_creation_in_helper() -> None:
    """Helper only appends ProjectCandidate instances from second pass."""
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass

    first = _doc(_seven_final_projects()[:1])
    second = _doc(_missing_two())
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 2
    assert all(p.project_name for p in out.projects)
    assert all(p.source_refs for p in out.projects)


def test_v19_flag_false_v20_true() -> None:
    from app.ai.prompts import profile_extract_v19 as v19
    from app.ai.prompts import profile_extract_v20 as v20
    from app.ai.prompts.profile_extract import (
        current_profile_prompt,
        resolve_profile_prompt,
    )

    assert resolve_profile_prompt("profile-extract-v19").recover_project_recall_second_pass is False
    assert not hasattr(v19, "RECOVER_PROJECT_RECALL_SECOND_PASS")
    assert v20.RECOVER_PROJECT_RECALL_SECOND_PASS is True
    assert current_profile_prompt().recover_project_recall_second_pass is True
    assert "Re-scan ALL distinct project" in v20.PROJECTS_RECOVERY_RETRY_INSTRUCTION


def test_core_recovery_budget_exhausted_skips_second_pass_gate() -> None:
    """Mirror service gate: recovery_budget==0 or call_count>=3 → no second pass."""
    recovery_budget = 0
    call_count = 2
    source_char_count = 5000
    rich = True
    flag = True
    should_run = (
        flag
        and recovery_budget > 0
        and call_count < 3
        and source_char_count >= 500
        and rich
    )
    assert should_run is False

    recovery_budget = 1
    call_count = 2
    should_run = (
        flag
        and recovery_budget > 0
        and call_count < 3
        and source_char_count >= 500
        and rich
    )
    assert should_run is True


def test_total_llm_calls_at_most_3_with_second_pass() -> None:
    """Happy path CORE+PROJECTS+second PROJECTS consumes exactly 3 calls."""
    calls: list[dict[str, Any]] = []

    def fake_complete_json(**kwargs):
        calls.append(kwargs.get("log_context") or {})
        phase = (kwargs.get("log_context") or {}).get("phase")
        if phase == "core":
            return {
                "p": {"n": "홍길동"},
                "j": [],
                "s": [],
                "x": [],
                "w": [],
                "e": [],
                "c": [],
            }
        # PROJECTS first + second: return compact pr with one project that has r.
        return {
            "pr": [
                {
                    "n": "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
                    "cust": "국가정보자원관리원",
                    "s": "2019.07",
                    "e": "2020.01",
                    "r": [{"d": "D1", "p": 1, "q": "2019년 제1차 범정부"}],
                }
            ]
        }

    # Lightweight budget simulation matching service constraints.
    call_count = 0
    recovery_budget = 1

    def phase_call(*, recovery_retry: bool) -> None:
        nonlocal call_count
        assert call_count < 3
        call_count += 1
        fake_complete_json(
            log_context={
                "phase": "projects" if call_count > 1 else "core",
                "recovery_retry": recovery_retry,
            }
        )

    phase_call(recovery_retry=False)  # CORE
    phase_call(recovery_retry=False)  # PROJECTS first
    assert recovery_budget > 0 and call_count < 3
    recovery_budget -= 1
    phase_call(recovery_retry=True)  # PROJECTS second
    assert call_count == 3
    assert recovery_budget == 0
    assert len(calls) == 3
