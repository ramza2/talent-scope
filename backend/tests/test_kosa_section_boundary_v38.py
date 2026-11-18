"""V38 KOSA page 1 work-history versus technical-project section boundary."""

from app.modules.analysis.candidate_quality_guards import (
    _classify_span,
    _iter_context_spans,
)

_DOC = "00000000-0000-0000-0000-000000000038"


def test_kosa_work_history_then_technical_projects_never_reopen_on_header() -> None:
    # KOSA p1: seven real employers followed by 기술경력 rows; the latter
    # may be transcribed as a mistaken "회사명 | 입사일 | 퇴사일" header.
    employers = "\n".join(
        f"근무처 에이회사{i} | 199{i}.01.01 ~ 199{i}.12.31"
        for i in range(7)
    )
    text = (
        "근무경력\n" + employers + "\n"
        "기술경력\n"
        "확인여부 | 참여사업명 | 참여기간 | 발주처 | 소속사\n"
        "AI 융합 컨설팅 | 2024.11.18 ~ 2025.01.10 | 지자체 | 협력사\n"
        "회사명 | 입사일 | 퇴사일 | 근무기간\n"
        "법률 지원 설계 | 2024.05.09 ~ 2024.09.30 | 법원 | 협력사\n"
    )
    spans = list(_iter_context_spans({(_DOC, 1): text}, {_DOC: "DOC-KOSA"}))

    work = [span for span in spans if "에이회사" in span.text]
    technical_headers = [
        span for span in spans
        if any(label in span.text for label in ("회사명", "입사일", "퇴사일"))
    ]
    technical_last = next(
        span for span in spans if "법률 지원 설계" in span.text
    )
    assert len(work) == 7
    assert all(
        _classify_span(span.text, section=span.section, doc_type=span.doc_type) == "employment"
        for span in work
    )
    assert technical_headers
    assert all(span.section == "project" for span in technical_headers)
    assert all(
        _classify_span(span.text, section=span.section, doc_type=span.doc_type) == "project"
        for span in technical_headers
    )
    assert technical_last.section == "project"


def test_explicit_new_kosa_employment_section_can_reopen_after_projects() -> None:
    text = (
        "근무경력\n"
        "에이회사 2001.01.01 ~ 2002.12.31\n"
        "기술경력\n"
        "참여기간 2024.05.09 ~ 2024.09.30\n"
        "회사명 | 입사일 | 퇴사일 | 근무기간\n"
        "근무경력\n"
        "비이회사 2003.01.01 ~ 2005.12.31\n"
    )
    spans = list(_iter_context_spans({(_DOC, 1): text}, {_DOC: "DOC-KOSA"}))
    after = next(span for span in spans if "비이회사" in span.text)
    assert after.section == "employment"


def test_non_kosa_generic_employment_section_behavior_is_unchanged() -> None:
    text = (
        "기술경력\n"
        "회사명 | 입사일 | 퇴사일 | 근무기간\n"
        "에이회사 2001.01.01 ~ 2002.12.31"
    )
    spans = list(_iter_context_spans({(_DOC, 1): text}, {_DOC: "DOC-RESUME"}))
    # The unmarked "회사명 |" prefix is emitted as a separate sticky
    # project-context span by the existing collapsed-line splitter.
    # The explicit 입사일/퇴사일 markers still reopen employment on resumes.
    markers = [
        span for span in spans
        if "입사일" in span.text or "퇴사일" in span.text
    ]
    assert markers
    assert all(span.section == "employment" for span in markers)
    assert all(
        _classify_span(span.text, section=span.section, doc_type=span.doc_type)
        == "employment"
        for span in markers
    )
