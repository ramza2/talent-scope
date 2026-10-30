"""PR #125: fail-closed PROJECT date reconciliation using exact root pages."""

from __future__ import annotations

from app.ai.schemas.profile_candidate import (
    AnalysisMetaCandidate,
    ProfileCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
    SummaryCandidate,
)
from app.modules.analysis.compact_v8 import (
    backfill_project_dates_from_root_evidence,
    _merge_project_relations_and_refs,
)

D1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
D2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _project(
    name="데이터 플랫폼 구축",
    customer="고객A",
    start="2005.12",
    end="2005.12",
    *,
    quote="DB 설계 담당",
    doc=D1,
    page=1,
):
    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        duration_months=0,
        source_refs=(
            [SourceRef(document_id=doc, page_no=page, quote_text=quote)]
            if quote else []
        ),
    )


def _candidate(*projects):
    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스터"),
        projects=list(projects),
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def _backfill(doc, pages):
    allowed = {}
    for doc_id, page_no in pages:
        allowed.setdefault(doc_id, set()).add(page_no)
    return backfill_project_dates_from_root_evidence(
        doc, page_texts=pages, allowed_documents=allowed
    )


ROW = "데이터 플랫폼 구축 | 2004.04 ~ 2005.12 | DB 설계 담당 | 고객A"


def test_collapsed_end_reconciles_on_unique_owned_root_page():
    source = _project()
    result = _backfill(_candidate(source), {(D1, 1): ROW}).projects[0]
    assert (result.start_date, result.end_date, result.duration_months) == (
        "2004.04", "2005.12", 20
    )
    assert result.source_refs == source.source_refs


def test_collapsed_start_reconciles_end_with_duration():
    source = _project(start="2004.04", end="2004.04")
    result = _backfill(_candidate(source), {(D1, 1): ROW}).projects[0]
    assert (result.start_date, result.end_date, result.duration_months) == (
        "2004.04", "2005.12", 20
    )


def test_real_one_month_project_remains_unchanged():
    source = _project(start="2005.12", end="2005.12")
    row = "데이터 플랫폼 구축 | 2005.12 ~ 2005.12 | DB 설계 담당 | 고객A"
    result = _backfill(_candidate(source), {(D1, 1): row}).projects[0]
    assert result == source


def test_mismatched_name_or_customer_never_borrows_period():
    rows = [
        "다른 구축사업 | 2004.04 ~ 2005.12 | DB 설계 담당 | 고객A",
        "데이터 플랫폼 구축 | 2004.04 ~ 2005.12 | DB 설계 담당 | 다른고객",
    ]
    for row in rows:
        source = _project()
        assert _backfill(_candidate(source), {(D1, 1): row}).projects[0] == source


def test_multi_project_page_uses_own_title_customer_and_quote():
    row_a = "데이터 플랫폼 구축 | 2004.04 ~ 2005.12 | DB 설계 담당 | 고객A"
    row_b = "보안 시스템 개선 | 2002.01 ~ 2005.12 | 보안 운영 담당 | 고객B"
    p_a = _project()
    p_b = _project(
        name="보안 시스템 개선", customer="고객B", quote="보안 운영 담당"
    )
    out = _backfill(
        _candidate(p_a, p_b), {(D1, 1): row_a + "\n" + row_b}
    ).projects
    assert [(p.start_date, p.end_date) for p in out] == [
        ("2004.04", "2005.12"),
        ("2002.01", "2005.12"),
    ]


def test_same_period_multiple_projects_requires_separate_identity():
    row_a = "데이터 플랫폼 구축 | 2004.04 ~ 2005.12 | DB 설계 담당 | 고객A"
    row_b = "보안 시스템 개선 | 2004.04 ~ 2005.12 | 보안 운영 담당 | 고객B"
    a = _project()
    b = _project(
        name="보안 시스템 개선", customer="고객B", quote="보안 운영 담당"
    )
    out = _backfill(_candidate(a, b), {(D1, 1): row_a + "\n" + row_b})
    assert [p.start_date for p in out.projects] == ["2004.04", "2004.04"]

    # Repeated candidate identity on the same page is not uniquely owned.
    duplicate = _project()
    ambiguous = _backfill(_candidate(a, duplicate), {(D1, 1): row_a})
    assert [p.start_date for p in ambiguous.projects] == ["2005.12"] * 2


def test_wrong_document_page_or_unquoted_root_is_not_used():
    source = _project(doc=D2)
    assert _backfill(_candidate(source), {(D1, 1): ROW}).projects[0] == source
    source = _project(page=2)
    assert _backfill(_candidate(source), {(D1, 1): ROW}).projects[0] == source
    source = _project(quote=None)
    assert _backfill(_candidate(source), {(D1, 1): ROW}).projects[0] == source


def test_conflicting_root_pages_and_multiple_periods_fail_closed():
    p = _project()
    p = p.model_copy(
        update={
            "source_refs": [
                SourceRef(document_id=D1, page_no=1, quote_text="DB 설계 담당"),
                SourceRef(document_id=D2, page_no=2, quote_text="DB 설계 담당"),
            ]
        }
    )
    pages = {
        (D1, 1): ROW,
        (D2, 2):
            "데이터 플랫폼 구축 | 2003.04 ~ 2005.12 | DB 설계 담당 | 고객A",
    }
    assert _backfill(_candidate(p), pages).projects[0] == p
    row2 = "데이터 플랫폼 구축 | 2003.04 ~ 2005.12 | DB 설계 담당 | 고객A"
    p = _project()
    assert _backfill(_candidate(p), {(D1, 1): ROW + "\n" + row2}).projects[0] == p


def test_noncollapsed_existing_dates_and_weak_identity_are_untouched():
    source = _project(start="2004.05", end="2005.12")
    assert _backfill(_candidate(source), {(D1, 1): ROW}).projects[0] == source
    source = _project(customer=None)
    assert _backfill(_candidate(source), {(D1, 1): ROW}).projects[0] == source


def test_merged_duration_is_derived_from_selected_dates():
    a = _project(start="2004.04", end="2005.12")
    b = _project(start="2004.04", end="2005.12")
    a = a.model_copy(update={"duration_months": 999})
    merged = _merge_project_relations_and_refs(
        [a, b],
        project_name="데이터 플랫폼 구축",
        customer_name="고객A",
        start_date="2004.04",
        end_date="2005.12",
    )
    assert merged.duration_months == 20
