"""Post-merge root JOB/EXP evidence supplement from same-code project relations."""

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


def _row(
    name: str,
    *,
    period: str = "2020.01~2020.12",
    duty: str,
    customer: str = "고객A",
) -> str:
    """PPTX-like career row used by project-owned span attribution."""
    return f"{name} | 개요 | {period} | {duty} | {customer}"


def _doc(
    *,
    jobs=None,
    expertise=None,
    projects=None,
):
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
                    confidence=item.get("confidence"),
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
                    confidence=item.get("confidence"),
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
                project_name=p["project_name"],
                customer_name=p.get("customer_name", "고객A"),
                start_date=p.get("start_date", "2020.01"),
                end_date=p.get("end_date", "2020.12"),
                jobs=_rels(p.get("jobs")),
                expertise=_rels(p.get("expertise")),
                skills=_rels(p.get("skills")),
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


def test_same_code_supporting_quote_supplements_root_job_and_exp() -> None:
    page = _row("사업A", duty="PM 역할 수행 및 정보시스템 운영")
    doc = _doc(
        jobs=[{"code": "JOB-MGT-PM", "raw_value": "JOB-MGT-PM", "job_type": "PRIMARY"}],
        expertise=[
            {
                "code": "EXP-INFRA",
                "raw_value": "EXP-INFRA",
                "evidence_type": "EXPLICIT",
                "confidence": 0.9,
            }
        ],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM 역할 수행")],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-INFRA",
                        "source_refs": [_ref("정보시스템 운영")],
                    }
                ],
                "source_refs": [_ref("사업A")],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})

    assert report["supplemented_jobs"] == ["JOB-MGT-PM"]
    assert report["supplemented_expertise"] == ["EXP-INFRA"]
    assert out.jobs[0].source_refs[0].quote_text == "PM 역할 수행"
    assert out.jobs[0].raw_value == "PM 역할 수행"
    assert out.jobs[0].job_type == "PRIMARY"
    assert out.expertise[0].source_refs[0].quote_text == "정보시스템 운영"
    assert out.expertise[0].raw_value == "정보시스템 운영"
    assert out.expertise[0].evidence_type == "EXPLICIT"
    assert out.expertise[0].confidence == 0.9
    assert len(out.projects) == 1
    assert out.projects[0].jobs[0].source_refs[0].quote_text == "PM 역할 수행"


def test_sec_ops_title_not_treated_as_exp_infra() -> None:
    """PROJECT enhanced rule: 정보보안시스템 운영 is SEC-OPS, not INFRA."""
    page = _row("보안사업", duty="정보보안시스템 운영")
    doc = _doc(
        expertise=[{"code": "EXP-INFRA"}],
        projects=[
            {
                "project_name": "보안사업",
                "expertise": [
                    {
                        "code": "EXP-INFRA",
                        "source_refs": [_ref("정보보안시스템 운영")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert out.expertise[0].source_refs == []
    assert report["supplemented_expertise"] == []
    assert report["unresolved_expertise"] == ["EXP-INFRA"]


def test_sec_ops_title_can_supplement_exp_sec_ops() -> None:
    page = _row("보안사업", duty="정보보안시스템 운영")
    doc = _doc(
        expertise=[{"code": "EXP-SEC-OPS", "raw_value": "EXP-SEC-OPS"}],
        projects=[
            {
                "project_name": "보안사업",
                "expertise": [
                    {
                        "code": "EXP-SEC-OPS",
                        "source_refs": [_ref("정보보안시스템 운영")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_expertise"] == ["EXP-SEC-OPS"]
    assert out.expertise[0].source_refs[0].quote_text == "정보보안시스템 운영"


def test_same_code_unrelated_quote_not_supplemented() -> None:
    page = _row("사업A", duty="일반 업무 수행 및 회의 참석")
    doc = _doc(
        jobs=[{"code": "JOB-MGT-PM"}],
        expertise=[{"code": "EXP-INFRA"}],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("일반 업무 수행")],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-INFRA",
                        "source_refs": [_ref("회의 참석")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == []
    assert report["supplemented_expertise"] == []
    assert report["unresolved_jobs"] == ["JOB-MGT-PM"]
    assert report["unresolved_expertise"] == ["EXP-INFRA"]
    assert out.jobs[0].source_refs == []
    assert out.expertise[0].source_refs == []


def test_other_project_evidence_not_borrowed() -> None:
    """Project B relation must not feed root via Project A's page presence."""
    page = (
        _row("사업A", period="2020.01~2020.06", duty="회의 참석")
        + "\n"
        + _row(
            "사업B",
            period="2021.01~2021.12",
            duty="PM 역할 수행 및 정보시스템 운영",
            customer="고객B",
        )
    )
    doc = _doc(
        jobs=[{"code": "JOB-MGT-PM"}],
        expertise=[{"code": "EXP-INFRA"}],
        projects=[
            {
                "project_name": "사업A",
                "start_date": "2020.01",
                "end_date": "2020.06",
                # Wrong: relation refs point at B's supporting quotes
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM 역할 수행")],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-INFRA",
                        "source_refs": [_ref("정보시스템 운영")],
                    }
                ],
            },
            {
                "project_name": "사업B",
                "start_date": "2021.01",
                "end_date": "2021.12",
                "customer_name": "고객B",
                "jobs": [],
                "expertise": [],
            },
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert out.jobs[0].source_refs == []
    assert out.expertise[0].source_refs == []
    assert "JOB-MGT-PM" in report["unresolved_jobs"]
    assert "EXP-INFRA" in report["unresolved_expertise"]


def test_same_document_other_page_not_linked() -> None:
    page1 = _row("사업A", duty="회의 참석")
    page2 = _row("사업A", duty="PM 역할 수행")
    doc = _doc(
        jobs=[{"code": "JOB-MGT-PM"}],
        projects=[
            {
                "project_name": "사업A",
                # Relation cites page 1, but supporting quote only exists on page 2
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM 역할 수행", page=1)],
                    }
                ],
            }
        ],
    )
    out, report = _run(
        doc,
        page_texts={(_DOC, 1): page1, (_DOC, 2): page2},
        allowed={_DOC: {1, 2}},
    )
    assert out.jobs[0].source_refs == []
    assert report["unresolved_jobs"] == ["JOB-MGT-PM"]


def test_metadata_only_root_refs_preserved_when_safe() -> None:
    page = _row("사업A", duty="PM 역할 수행")
    doc = _doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "JOB-MGT-PM",
                "source_refs": [
                    {"document_id": _DOC, "page_no": 1, "quote_text": None}
                ],
            }
        ],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM 역할 수행")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == ["JOB-MGT-PM"]
    assert len(out.jobs[0].source_refs) == 1
    assert out.jobs[0].source_refs[0].document_id == _DOC
    assert out.jobs[0].source_refs[0].page_no == 1
    assert out.jobs[0].source_refs[0].quote_text == "PM 역할 수행"


def test_metadata_only_conflicting_doc_or_page_not_overwritten() -> None:
    page = _row("사업A", duty="PM 역할 수행")
    doc = _doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "source_refs": [
                    {
                        "document_id": _DOC_OTHER,
                        "page_no": 1,
                        "quote_text": "",
                    }
                ],
            }
        ],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM 역할 수행")],
                    }
                ],
            }
        ],
    )
    out, report = _run(
        doc,
        page_texts={(_DOC, 1): page, (_DOC_OTHER, 1): "다른문서"},
        allowed={_DOC: {1}, _DOC_OTHER: {1}},
    )
    assert report["supplemented_jobs"] == []
    assert report["unresolved_jobs"] == ["JOB-MGT-PM"]
    assert out.jobs[0].source_refs[0].document_id == _DOC_OTHER
    assert not (out.jobs[0].source_refs[0].quote_text or "").strip()


def test_existing_quoted_root_refs_preserved_no_overwrite() -> None:
    page = _row("사업A", duty="기존 원문 PM 과 정보시스템 운영")
    doc = _doc(
        jobs=[
            {
                "code": "JOB-MGT-PM",
                "raw_value": "기존 PM",
                "source_refs": [_ref("기존 원문 PM")],
            }
        ],
        expertise=[
            {
                "code": "EXP-INFRA",
                "raw_value": "인프라",
                "source_refs": [_ref("정보시스템 운영")],
            }
        ],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-MGT-PM",
                        "source_refs": [_ref("PM")],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-INFRA",
                        "source_refs": [_ref("정보시스템 운영")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert report["supplemented_jobs"] == []
    assert report["supplemented_expertise"] == []
    assert out.jobs[0].source_refs[0].quote_text == "기존 원문 PM"
    assert out.jobs[0].raw_value == "기존 PM"
    assert out.expertise[0].raw_value == "인프라"


def test_codes_without_semantic_rules_remain_unresolved() -> None:
    page = _row("사업A", duty="AI개발자 RAG 전문")
    doc = _doc(
        jobs=[{"code": "JOB-DEV-GEN", "raw_value": "JOB-DEV-GEN"}],
        expertise=[{"code": "EXP-AI-RAG", "raw_value": "EXP-AI-RAG"}],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-DEV-GEN",
                        "source_refs": [_ref("AI개발자")],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-AI-RAG",
                        "source_refs": [_ref("RAG 전문")],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert out.jobs[0].source_refs == []
    assert out.expertise[0].source_refs == []
    assert out.jobs[0].raw_value == "JOB-DEV-GEN"
    assert report["unresolved_jobs"] == ["JOB-DEV-GEN"]
    assert report["unresolved_expertise"] == ["EXP-AI-RAG"]


def test_metadata_only_ref_without_quote_on_relation_not_supplemented() -> None:
    page = _row("사업A", duty="시스템 운영 및 사업관리")
    doc = _doc(
        jobs=[{"code": "JOB-OPS-SYS"}],
        expertise=[{"code": "EXP-MGT"}],
        projects=[
            {
                "project_name": "사업A",
                "jobs": [
                    {
                        "code": "JOB-OPS-SYS",
                        "source_refs": [
                            {
                                "document_id": _DOC,
                                "page_no": 1,
                                "quote_text": None,
                            },
                            {
                                "document_id": _DOC,
                                "page_no": 1,
                                "quote_text": "   ",
                            },
                        ],
                    }
                ],
                "expertise": [
                    {
                        "code": "EXP-MGT",
                        "source_refs": [{"document_id": _DOC, "page_no": 1}],
                    }
                ],
            }
        ],
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert out.jobs[0].source_refs == []
    assert out.expertise[0].source_refs == []
    assert report["supplemented_jobs"] == []
    assert report["supplemented_expertise"] == []


def test_entity_counts_and_project_count_stable() -> None:
    page = "\n".join(
        _row(f"사업{i}", period=f"202{i}.01~202{i}.12", duty="PM 역할 사업관리")
        for i in range(3)
    )
    projects = [
        {
            "project_name": f"사업{i}",
            "start_date": f"202{i}.01",
            "end_date": f"202{i}.12",
            "jobs": [
                {
                    "code": "JOB-MGT-PM",
                    "source_refs": [_ref("PM 역할")],
                }
            ],
            "expertise": [
                {
                    "code": "EXP-MGT",
                    "source_refs": [_ref("사업관리")],
                }
            ],
        }
        for i in range(3)
    ]
    doc = _doc(
        jobs=[{"code": "JOB-MGT-PM"}, {"code": "JOB-OPS-SYS"}],
        expertise=[{"code": "EXP-MGT"}, {"code": "EXP-AI-RAG"}],
        projects=projects,
    )
    out, report = _run(doc, page_texts={(_DOC, 1): page})
    assert len(out.jobs) == 2
    assert len(out.expertise) == 2
    assert len(out.projects) == 3
    assert "JOB-MGT-PM" in report["supplemented_jobs"]
    assert "EXP-MGT" in report["supplemented_expertise"]
    assert "JOB-OPS-SYS" in report["unresolved_jobs"]
    assert "EXP-AI-RAG" in report["unresolved_expertise"]
