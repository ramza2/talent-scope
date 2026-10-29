"""Multi-line DOCX/PDF project ownership for relation evidence attribution."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC = "a1111111-1111-1111-1111-111111111111"
_DOC_OTHER = "b2222222-2222-2222-2222-222222222222"


def _ref(quote: str, *, doc: str = _DOC, page: int = 1) -> dict:
    return {"document_id": doc, "page_no": page, "quote_text": quote}


def _pptx_row(
    name: str,
    *,
    period: str = "2020.01~2020.12",
    duty: str,
    customer: str = "고객A",
) -> str:
    return f"{name} | 개요 | {period} | {duty} | {customer}"


def _multiline_block(
    *,
    period: str,
    name: str | None,
    customer: str,
    duties: list[str],
    fillers: int = 0,
    name_after_period: bool = True,
) -> str:
    """Synthetic DOCX/PDF table extraction with fields on separate lines."""
    lines: list[str] = []
    if not name_after_period and name:
        lines.append(name)
    lines.append(period)
    if name_after_period and name:
        lines.append(name)
    lines.extend(f"설명줄{i}" for i in range(fillers))
    lines.append(customer)
    lines.append("담당업무")
    lines.extend(duties)
    return "\n".join(lines)


def _doc(*, jobs=None, expertise=None, projects=None):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        CodeRefCandidate,
        ExpertiseCandidate,
        JobCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        ProjectCandidate,
        SourceRef,
        SummaryCandidate,
    )

    def _jobs(items):
        out = []
        for item in items or []:
            refs = [
                SourceRef(
                    document_id=r.get("document_id"),
                    page_no=r.get("page_no"),
                    quote_text=r.get("quote_text"),
                )
                for r in (item.get("source_refs") or [])
            ]
            out.append(
                JobCandidate(
                    code=item["code"],
                    raw_value=item.get("raw_value", item["code"]),
                    job_type=item.get("job_type", "PRIMARY"),
                    source_refs=refs,
                )
            )
        return out

    def _exps(items):
        out = []
        for item in items or []:
            refs = [
                SourceRef(
                    document_id=r.get("document_id"),
                    page_no=r.get("page_no"),
                    quote_text=r.get("quote_text"),
                )
                for r in (item.get("source_refs") or [])
            ]
            out.append(
                ExpertiseCandidate(
                    code=item["code"],
                    raw_value=item.get("raw_value", item["code"]),
                    evidence_type=item.get("evidence_type", "EXPLICIT"),
                    source_refs=refs,
                )
            )
        return out

    def _rels(items):
        out = []
        for item in items or []:
            refs = [
                SourceRef(
                    document_id=r.get("document_id"),
                    page_no=r.get("page_no"),
                    quote_text=r.get("quote_text"),
                )
                for r in (item.get("source_refs") or [])
            ]
            out.append(
                CodeRefCandidate(
                    code=item["code"],
                    raw_value=item.get("raw_value"),
                    source_refs=refs,
                )
            )
        return out

    project_rows = []
    for p in projects or []:
        project_rows.append(
            ProjectCandidate(
                project_name=p.get("project_name"),
                customer_name=p.get("customer_name", "고객A"),
                start_date=p.get("start_date", "2020.01"),
                end_date=p.get("end_date", "2020.12"),
                jobs=_rels(p.get("jobs")),
                expertise=_rels(p.get("expertise")),
                source_refs=[
                    SourceRef(
                        document_id=r["document_id"],
                        page_no=r["page_no"],
                        quote_text=r["quote_text"],
                    )
                    for r in (p.get("source_refs") or [])
                ],
            )
        )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        analysis=AnalysisMetaCandidate(),
        profile=ProfileCandidate(name="테스터"),
        jobs=_jobs(jobs),
        expertise=_exps(expertise),
        projects=project_rows,
        summary=SummaryCandidate(profile_summary=None),
    )


def _run(doc, *, page_texts, allowed=None):
    from app.modules.analysis.compact_v8 import (
        supplement_root_job_exp_evidence_from_project_relations,
    )

    return supplement_root_job_exp_evidence_from_project_relations(
        doc,
        page_texts=page_texts,
        allowed_documents=allowed or {_DOC: {1, 2}},
    )


def test_multiline_name_period_duty_separated_allows_unique_span() -> None:
    """Name/period/duty 10~15 lines apart still attribute when uniquely bounded."""
    page = "\n".join(
        [
            "경력",
            _multiline_block(
                period="2020.01~2020.12",
                name="데이터사업",
                customer="고객A",
                duties=["DA 컨설팅", "닷넷 개발", "BI/DW DB 모델러"],
                fillers=12,
                name_after_period=True,
            ),
            _multiline_block(
                period="2021.01~2021.12",
                name="사업B",
                customer="고객B",
                duties=["회의 참석"],
                fillers=2,
            ),
        ]
    )
    doc = _doc(
        jobs=[
            {"code": "JOB-DATA-DA"},
            {"code": "JOB-DEV-GEN"},
        ],
        expertise=[{"code": "EXP-DATA"}],
        projects=[
            {
                "project_name": "데이터사업",
                "customer_name": "고객A",
                "start_date": "2020.01",
                "end_date": "2020.12",
                "jobs": [
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅")],
                    },
                    {
                        "code": "JOB-DEV-GEN",
                        "source_refs": [_ref("닷넷 개발")],
                    },
                ],
                "expertise": [
                    {
                        "code": "EXP-DATA",
                        "source_refs": [_ref("BI/DW DB 모델러")],
                    }
                ],
            },
            {
                "project_name": "사업B",
                "customer_name": "고객B",
                "start_date": "2021.01",
                "end_date": "2021.12",
                "jobs": [],
                "expertise": [],
            },
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert set(report["supplemented_jobs"]) == {"JOB-DATA-DA", "JOB-DEV-GEN"}
    assert report["supplemented_expertise"] == ["EXP-DATA"]
    assert out.jobs[0].source_refs[0].quote_text == "DA 컨설팅"
    assert out.jobs[1].source_refs[0].quote_text == "닷넷 개발"
    assert out.expertise[0].source_refs[0].quote_text == "BI/DW DB 모델러"


def test_multiline_other_project_duty_not_borrowed() -> None:
    page = "\n".join(
        [
            _multiline_block(
                period="2020.01~2020.06",
                name="사업A",
                customer="고객A",
                duties=["회의 참석"],
            ),
            _multiline_block(
                period="2021.01~2021.12",
                name="사업B",
                customer="고객B",
                duties=["DA 컨설팅", "닷넷 개발"],
            ),
        ]
    )
    doc = _doc(
        jobs=[{"code": "JOB-DATA-DA"}, {"code": "JOB-DEV-GEN"}],
        projects=[
            {
                "project_name": "사업A",
                "start_date": "2020.01",
                "end_date": "2020.06",
                "jobs": [
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅")],
                    },
                    {
                        "code": "JOB-DEV-GEN",
                        "source_refs": [_ref("닷넷 개발")],
                    },
                ],
            },
            {
                "project_name": "사업B",
                "customer_name": "고객B",
                "start_date": "2021.01",
                "end_date": "2021.12",
                "jobs": [],
            },
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == []
    assert set(report["unresolved_jobs"]) == {"JOB-DATA-DA", "JOB-DEV-GEN"}
    assert all(not j.source_refs for j in out.jobs)


def test_multiline_duplicate_customer_period_nameless_blocked() -> None:
    page = "\n".join(
        [
            _multiline_block(
                period="2020.01~2020.12",
                name=None,
                customer="고객X",
                duties=["DA 컨설팅"],
            ),
            _multiline_block(
                period="2020.01~2020.06",
                name=None,
                customer="고객X",
                duties=["닷넷 개발"],
            ),
        ]
    )
    doc = _doc(
        jobs=[{"code": "JOB-DATA-DA"}],
        projects=[
            {
                "project_name": None,
                "customer_name": "고객X",
                "start_date": "2020.01",
                "end_date": "2020.12",
                "jobs": [
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == []
    assert report["unresolved_jobs"] == ["JOB-DATA-DA"]
    assert out.jobs[0].source_refs == []


def test_multiline_nameless_unique_customer_period_allows() -> None:
    page = "\n".join(
        [
            _multiline_block(
                period="2020.01~2020.12",
                name=None,
                customer="고객A",
                duties=["DBA"],
                fillers=8,
            ),
            _multiline_block(
                period="2021.01~2021.12",
                name=None,
                customer="고객B",
                duties=["회의"],
            ),
        ]
    )
    doc = _doc(
        jobs=[{"code": "JOB-DATA-DBA"}],
        projects=[
            {
                "project_name": None,
                "customer_name": "고객A",
                "start_date": "2020.01",
                "end_date": "2020.12",
                "jobs": [
                    {"code": "JOB-DATA-DBA", "source_refs": [_ref("DBA")]}
                ],
            },
            {
                "project_name": None,
                "customer_name": "고객B",
                "start_date": "2021.01",
                "end_date": "2021.12",
                "jobs": [],
            },
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == ["JOB-DATA-DBA"]
    assert out.jobs[0].source_refs[0].quote_text == "DBA"


def test_multiline_other_doc_or_page_blocked() -> None:
    page1 = _multiline_block(
        period="2020.01~2020.12",
        name="사업A",
        customer="고객A",
        duties=["회의"],
    )
    page2 = _multiline_block(
        period="2020.01~2020.12",
        name="사업A",
        customer="고객A",
        duties=["DA 컨설팅"],
    )
    doc = _doc(
        jobs=[{"code": "JOB-DATA-DA"}],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅", page=1)],
                    }
                ],
            }
        ],
    )
    out, report = _run(
        doc,
        page_texts={(_DOC, 1): page1, (_DOC, 2): page2},
    )
    assert out.jobs[0].source_refs == []
    assert report["unresolved_jobs"] == ["JOB-DATA-DA"]

    # Quote lives only under another document's different project block.
    other_page = _multiline_block(
        period="2020.01~2020.12",
        name="다른사업",
        customer="고객Z",
        duties=["DA 컨설팅"],
    )
    doc2 = _doc(
        jobs=[{"code": "JOB-DATA-DA"}],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅", doc=_DOC_OTHER)],
                    }
                ],
            },
            {
                "project_name": "다른사업",
                "customer_name": "고객Z",
                "jobs": [],
            },
        ],
    )
    out2, report2 = _run(
        doc2,
        page_texts={
            (_DOC, 1): page1,
            (_DOC_OTHER, 1): other_page,
        },
        allowed={_DOC: {1}, _DOC_OTHER: {1}},
    )
    assert out2.jobs[0].source_refs == []
    assert report2["unresolved_jobs"] == ["JOB-DATA-DA"]


def test_pptx_single_and_multi_row_still_work() -> None:
    page = (
        _pptx_row("데이터사업", duty="DBA / DA 컨설팅")
        + "\n"
        + _pptx_row(
            "사업B",
            period="2021.01~2021.12",
            duty="회의",
            customer="고객B",
        )
    )
    doc = _doc(
        jobs=[{"code": "JOB-DATA-DBA"}, {"code": "JOB-DATA-DA"}],
        projects=[
            {
                "project_name": "데이터사업",
                "jobs": [
                    {"code": "JOB-DATA-DBA", "source_refs": [_ref("DBA")]},
                    {
                        "code": "JOB-DATA-DA",
                        "source_refs": [_ref("DA 컨설팅")],
                    },
                ],
            },
            {
                "project_name": "사업B",
                "customer_name": "고객B",
                "start_date": "2021.01",
                "end_date": "2021.12",
                "jobs": [],
            },
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert set(report["supplemented_jobs"]) == {"JOB-DATA-DBA", "JOB-DATA-DA"}
    assert out.jobs[0].source_refs[0].quote_text == "DBA"
    assert out.jobs[1].source_refs[0].quote_text == "DA 컨설팅"


def test_db_tuning_still_rejects_dba_alone_on_multiline() -> None:
    page = _multiline_block(
        period="2020.01~2020.12",
        name="튜닝사업",
        customer="고객A",
        duties=["DBA", "DB 운영"],
        fillers=10,
    )
    doc = _doc(
        expertise=[{"code": "EXP-DATA-DB-TUNING"}],
        projects=[
            {
                "project_name": "튜닝사업",
                "expertise": [
                    {
                        "code": "EXP-DATA-DB-TUNING",
                        "source_refs": [_ref("DBA")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_expertise"] == []
    assert report["unresolved_expertise"] == ["EXP-DATA-DB-TUNING"]
    assert out.expertise[0].source_refs == []
