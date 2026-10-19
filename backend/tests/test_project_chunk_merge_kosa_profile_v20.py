"""DOC-KOSA ↔ DOC-PROFILE complementary adaptive PROJECT chunk merge."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC_KOSA = "e3bcbf71-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC_PROFILE = "e61541a2-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DOC_OTHER = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_TYPE_MAP = {
    _DOC_KOSA: "DOC-KOSA",
    _DOC_PROFILE: "DOC-PROFILE",
}


def _ref(quote: str, *, document_id: str, page_no: int = 1):
    from app.ai.schemas.profile_candidate import SourceRef

    return SourceRef(document_id=document_id, page_no=page_no, quote_text=quote)


def _proj(
    name: str,
    customer: str,
    start: str,
    end: str,
    *,
    doc: str,
    jobs=None,
    expertise=None,
    skills=None,
    responsibilities: str | None = None,
    project_summary: str | None = None,
):
    from app.ai.schemas.profile_candidate import CodeRefCandidate, ProjectCandidate

    def rels(items):
        out = []
        for code, quote in items or []:
            out.append(
                CodeRefCandidate(
                    code=code,
                    raw_value=code,
                    source_refs=[_ref(quote, document_id=doc)],
                )
            )
        return out

    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        responsibilities=responsibilities,
        project_summary=project_summary,
        source_refs=[_ref(name, document_id=doc)],
        jobs=rels(jobs),
        expertise=rels(expertise),
        skills=rels(skills),
    )


def _chunk(projects):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="강태련"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _view(doc):
    rows = []
    for p in doc.projects:
        rows.append(
            (
                p.project_name,
                p.customer_name,
                p.start_date,
                p.end_date,
                tuple(sorted({j.code for j in p.jobs})),
                tuple(sorted({x.code for x in p.expertise})),
                tuple(
                    sorted(
                        (r.document_id, r.page_no, r.quote_text)
                        for r in p.source_refs
                    )
                ),
            )
        )
    return sorted(rows)


def _dump_projects(doc):
    return [p.model_dump(mode="json") for p in doc.projects]


def _kosa_live_rows():
    """Live run 14723c93-5c5a-420d-ae74-36e78689f2f7 KOSA rows (8)."""
    return [
        _proj(
            "2011년 HW자원통합구축 사업2-1(대전)",
            "(주)투루텍",
            "2011.06.10",
            "2011.11.15",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2011년 HW자원통합구축 사업2-1(대전)",
            "(주)투루텍",
            "2011.11.16",
            "2011.12.24",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2013년 차기 복권통합구축사업",
            "인사이텍 주식회사",
            "2013.09.16",
            "2013.12.31",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "국방통합정보관리소 정보체계 이전/통합 사업",
            "(주)타이호인스트",
            "2014.02.07",
            "2014.12.06",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2016년 HW자원통합구축 사업2-2(광주)",
            "(주)아이티센",
            "2016.08.25",
            "2017.03.25",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2017년 제2차 정보자원통합구축 HW1사업 (대전)",
            "(주)에스제이정보통신",
            "2017.10.19",
            "2018.03.22",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            "(주)휴먼정보",
            "2018.06.11",
            "2019.01.10",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
            "(주)휴먼정보",
            "2019.07.15",
            "2020.01.15",
            doc=_DOC_KOSA,
            jobs=[("JOB-OPS-SYS", "시스템 운영")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
    ]


def _profile_live_rows():
    """Live run 14723c93-5c5a-420d-ae74-36e78689f2f7 PROFILE rows (9)."""
    return [
        _proj(
            "2008년 정부통합전산센터 BCP 관련 BIA 구축 사업",
            "행정안전부",
            "2008.09",
            "2008.12",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
        ),
        _proj(
            "영화진흥위원회 전산실 유지보수 사업",
            "영화진흥위원회",
            "2008.12",
            "2010.04",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
        ),
        _proj(
            "2011년 제2차 자원통합 구축 HW1 사업(대전)",
            "정부통합 전산센터",
            "2011.06",
            "2012.01",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
        _proj(
            "2013년 차기 복권통합구축사업",
            "복권위원회",
            "2013.09",
            "2013.12",
            doc=_DOC_PROFILE,
            jobs=[("JOB-ARC-TA", "TA")],
            expertise=[("EXP-INFRA", "인프라")],
        ),
        _proj(
            "국방 메가센터 정보시스템 이전통합사업(육군)",
            "국방부",
            "2014.02",
            "2014.12",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
        _proj(
            "2016년 제2차 범정부 정보자원 통합구축 HW2 사업",
            "정부통합 전산센터",
            "2016.08",
            "2017.03",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
        _proj(
            "2017년 제2차 범정부 정보자원 통합구축 HW1 사업",
            "국가정보자원관리원",
            "2017.10",
            "2018.03",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
        _proj(
            "2018년 제1차 범정부 정보자원 통합구축 HW2 사업",
            "국가정보자원관리원",
            "2018.06",
            "2019.01",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
        _proj(
            "2019년 제1차 범정부 정보자원 통합구축 HW1 사업",
            "국가정보자원관리원",
            "2019.07",
            "2020.01",
            doc=_DOC_PROFILE,
            jobs=[("JOB-MGT", "사업관리")],
            expertise=[("EXP-MGT", "사업관리")],
        ),
    ]


def test_live_kosa_profile_merge_yields_nine_order_independent() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    kosa = _kosa_live_rows()
    profile = _profile_live_rows()
    forward = merge_project_chunk_candidates(
        [_chunk(kosa), _chunk(profile)], document_type_by_id=_TYPE_MAP
    )
    reverse = merge_project_chunk_candidates(
        [_chunk(list(reversed(profile))), _chunk(list(reversed(kosa)))],
        document_type_by_id=_TYPE_MAP,
    )
    assert len(forward.projects) == 9
    assert _view(forward) == _view(reverse)
    assert _dump_projects(forward) == _dump_projects(reverse)

    # 2019 dual relations + customer from PROFILE + KOSA day/name preference
    p2019 = next(
        p for p in forward.projects if (p.start_date or "").startswith("2019")
    )
    assert {j.code for j in p2019.jobs} == {"JOB-OPS-SYS", "JOB-MGT"}
    assert {x.code for x in p2019.expertise} == {"EXP-INFRA", "EXP-MGT"}
    assert p2019.customer_name == "국가정보자원관리원"
    assert {r.document_id for r in p2019.source_refs} == {_DOC_KOSA, _DOC_PROFILE}

    p2013 = next(
        p for p in forward.projects if (p.start_date or "").startswith("2013")
    )
    assert {j.code for j in p2013.jobs} == {"JOB-OPS-SYS", "JOB-ARC-TA"}
    assert "EXP-INFRA" in {x.code for x in p2013.expertise}
    assert p2013.customer_name == "복권위원회"
    assert {r.document_id for r in p2013.source_refs} == {_DOC_KOSA, _DOC_PROFILE}

    p2011 = next(
        p for p in forward.projects if (p.start_date or "").startswith("2011")
    )
    assert p2011.project_name == "2011년 제2차 자원통합 구축 HW1 사업(대전)"
    assert p2011.customer_name == "정부통합 전산센터"
    assert p2011.start_date == "2011.06.10"
    assert p2011.end_date == "2011.12.24"
    assert {j.code for j in p2011.jobs} == {"JOB-OPS-SYS", "JOB-MGT"}
    assert {x.code for x in p2011.expertise} == {"EXP-INFRA", "EXP-MGT"}
    assert len(p2011.jobs) == len({j.code for j in p2011.jobs})
    assert {r.document_id for r in p2011.source_refs} == {_DOC_KOSA, _DOC_PROFILE}

    names = {p.project_name for p in forward.projects}
    assert "영화진흥위원회 전산실 유지보수 사업" in names
    assert any("BCP" in (n or "") or "BIA" in (n or "") for n in names)


def test_missing_doc_type_map_keeps_strict_only() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    kosa = _kosa_live_rows()
    profile = _profile_live_rows()
    out = merge_project_chunk_candidates([_chunk(kosa), _chunk(profile)])
    # Live customers differ across docs → no strict identity collapse.
    assert len(out.projects) == 17
    p2019s = [
        p for p in out.projects if (p.start_date or "").startswith("2019")
    ]
    assert len(p2019s) == 2


def test_same_document_relaxed_merge_forbidden() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "정부통합전산센터",
        "2019.07",
        "2020.01",
        doc=_DOC_KOSA,
    )
    b = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "국가정보자원관리원",
        "2019.07",
        "2020.01",
        doc=_DOC_KOSA,
    )
    out = merge_project_chunk_candidates(
        [_chunk([a]), _chunk([b])],
        document_type_by_id={_DOC_KOSA: "DOC-KOSA", _DOC_PROFILE: "DOC-PROFILE"},
    )
    assert len(out.projects) == 2


def test_non_kosa_profile_type_pair_forbidden() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "정부통합전산센터",
        "2019.07",
        "2020.01",
        doc=_DOC_KOSA,
    )
    b = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "국가정보자원관리원",
        "2019.07",
        "2020.01",
        doc=_DOC_OTHER,
    )
    out = merge_project_chunk_candidates(
        [_chunk([a]), _chunk([b])],
        document_type_by_id={
            _DOC_KOSA: "DOC-KOSA",
            _DOC_OTHER: "DOC-RESUME",
            _DOC_PROFILE: "DOC-PROFILE",
        },
    )
    assert len(out.projects) == 2


def test_weak_unrelated_title_same_dates_not_merged() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj(
        "2019년 네트워크 교체 사업",
        "정부통합전산센터",
        "2019.07",
        "2020.01",
        doc=_DOC_KOSA,
    )
    b = _proj(
        "2019년 클라우드 이전 컨설팅",
        "국가정보자원관리원",
        "2019.07",
        "2020.01",
        doc=_DOC_PROFILE,
    )
    out = merge_project_chunk_candidates(
        [_chunk([a]), _chunk([b])], document_type_by_id=_TYPE_MAP
    )
    assert len(out.projects) == 2


def test_ambiguous_one_to_many_non_aggregate_not_merged() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    kosa = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "정부통합전산센터",
        "2019.07",
        "2020.01",
        doc=_DOC_KOSA,
    )
    p1 = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1사업",
        "국가정보자원관리원",
        "2019.07",
        "2020.01",
        doc=_DOC_PROFILE,
    )
    p2 = _proj(
        "2019년 제1차 범정부 정보자원 통합구축 HW1 후속사업",
        "국가정보자원관리원",
        "2019.07",
        "2020.01",
        doc=_DOC_PROFILE,
    )
    out = merge_project_chunk_candidates(
        [_chunk([kosa]), _chunk([p1, p2])], document_type_by_id=_TYPE_MAP
    )
    assert len(out.projects) == 3


def test_aggregate_exact_same_segment_set_ambiguous_no_merge() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    k1 = _proj(
        "2011년 HW자원통합구축 사업2-1(대전)",
        "(주)투루텍",
        "2011.06.10",
        "2011.11.15",
        doc=_DOC_KOSA,
    )
    k2 = _proj(
        "2011년 HW자원통합구축 사업2-1(대전)",
        "(주)투루텍",
        "2011.11.16",
        "2011.12.24",
        doc=_DOC_KOSA,
    )
    p1 = _proj(
        "2011년 제2차 자원통합 구축 HW1 사업(대전)",
        "정부통합 전산센터",
        "2011.06",
        "2012.01",
        doc=_DOC_PROFILE,
    )
    p2 = _proj(
        "2011년 제2차 자원통합구축 HW1사업(대전) 요약",
        "국가정보자원관리원",
        "2011.06",
        "2012.01",
        doc=_DOC_PROFILE,
    )
    out = merge_project_chunk_candidates(
        [_chunk([k1, k2]), _chunk([p1, p2])], document_type_by_id=_TYPE_MAP
    )
    assert len(out.projects) == 4


def test_aggregate_partial_overlap_segment_sets_ambiguous_no_merge() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    # Three contiguous KOSA segments; two PROFILE aggregates share the middle.
    ka = _proj(
        "2011년 HW자원통합구축 사업 A",
        "(주)투루텍",
        "2011.01.01",
        "2011.04.30",
        doc=_DOC_KOSA,
    )
    kb = _proj(
        "2011년 HW자원통합구축 사업 B",
        "(주)투루텍",
        "2011.05.01",
        "2011.08.31",
        doc=_DOC_KOSA,
    )
    kc = _proj(
        "2011년 HW자원통합구축 사업 C",
        "(주)투루텍",
        "2011.09.01",
        "2011.12.31",
        doc=_DOC_KOSA,
    )
    p_ab = _proj(
        "2011년 제2차 자원통합 구축 HW 사업 AB",
        "정부통합 전산센터",
        "2011.01",
        "2011.08",
        doc=_DOC_PROFILE,
    )
    p_bc = _proj(
        "2011년 제2차 자원통합 구축 HW 사업 BC",
        "국가정보자원관리원",
        "2011.05",
        "2011.12",
        doc=_DOC_PROFILE,
    )
    out = merge_project_chunk_candidates(
        [_chunk([ka, kb, kc]), _chunk([p_ab, p_bc])],
        document_type_by_id=_TYPE_MAP,
    )
    assert len(out.projects) == 5


def test_aggregate_disjoint_segment_sets_both_merge() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    k11a = _proj(
        "2011년 HW자원통합구축 사업2-1(대전)",
        "(주)투루텍",
        "2011.06.10",
        "2011.11.15",
        doc=_DOC_KOSA,
    )
    k11b = _proj(
        "2011년 HW자원통합구축 사업2-1(대전)",
        "(주)투루텍",
        "2011.11.16",
        "2011.12.24",
        doc=_DOC_KOSA,
    )
    k16a = _proj(
        "2016년 HW자원통합구축 사업2-2(광주)",
        "(주)아이티센",
        "2016.08.25",
        "2016.11.30",
        doc=_DOC_KOSA,
    )
    k16b = _proj(
        "2016년 HW자원통합구축 사업2-2(광주)",
        "(주)아이티센",
        "2016.12.01",
        "2017.03.25",
        doc=_DOC_KOSA,
    )
    p11 = _proj(
        "2011년 제2차 자원통합 구축 HW1 사업(대전)",
        "정부통합 전산센터",
        "2011.06",
        "2012.01",
        doc=_DOC_PROFILE,
    )
    p16 = _proj(
        "2016년 제2차 범정부 정보자원 통합구축 HW2 사업",
        "정부통합 전산센터",
        "2016.08",
        "2017.03",
        doc=_DOC_PROFILE,
    )
    out = merge_project_chunk_candidates(
        [_chunk([k11a, k11b, k16a, k16b]), _chunk([p11, p16])],
        document_type_by_id=_TYPE_MAP,
    )
    assert len(out.projects) == 2
    starts = sorted((p.start_date or "")[:4] for p in out.projects)
    assert starts == ["2011", "2016"]


def test_missing_identity_singles_serialized_order_independent() -> None:
    """Incomplete-identity singles with differing jobs/refs must dump identically."""
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    # Same name/customer/empty dates/doc/root quote; different jobs + responsibilities.
    # Missing dates keep identity incomplete (no false merge).
    a = _proj(
        "미확인 사업",
        "고객A",
        "",
        "",
        doc=_DOC_KOSA,
        jobs=[("JOB-OPS-SYS", "운영")],
        responsibilities="서버 운영",
    )
    b = _proj(
        "미확인 사업",
        "고객A",
        "",
        "",
        doc=_DOC_KOSA,
        jobs=[("JOB-MGT", "관리")],
        responsibilities="사업 관리",
    )
    forward = merge_project_chunk_candidates([_chunk([a, b])])
    reverse = merge_project_chunk_candidates([_chunk([b, a])])
    assert len(forward.projects) == 2
    assert _dump_projects(forward) == _dump_projects(reverse)
    assert forward.projects[0].jobs[0].code != forward.projects[1].jobs[0].code


def test_strict_exact_identity_still_merges_without_type_map() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj(
        "동일사업",
        "동일고객",
        "2020.01",
        "2020.12",
        doc=_DOC_KOSA,
        jobs=[("JOB-MGT", "사업관리")],
    )
    b = _proj(
        "동일사업",
        "동일고객",
        "2020.01",
        "2020.12",
        doc=_DOC_PROFILE,
        jobs=[("JOB-OPS-SYS", "운영")],
    )
    out = merge_project_chunk_candidates([_chunk([a]), _chunk([b])])
    assert len(out.projects) == 1
    assert {j.code for j in out.projects[0].jobs} == {"JOB-MGT", "JOB-OPS-SYS"}
