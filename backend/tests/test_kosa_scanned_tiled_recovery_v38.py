"""V38 scanned KOSA quality fallback: rule-free image tiles, DB-free tests."""

from __future__ import annotations

from uuid import uuid4

import pymupdf
import pytest

from app.ai.providers.vlm import FakeVLMProvider
from app.core.config import Settings
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
)
from app.modules.analysis.vlm_table_banding import (
    is_kosa_empty_markdown_table_vlm_quality_issue,
    is_plausible_kosa_employment_recovery,
)


def _empty_kosa_page() -> str:
    return "소프트웨어기술자 경력증명서\n회사명\n" + (
        "|  |  |  |  |  |\n" * 420
    )


def _scan_pdf() -> bytes:
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 70), "Scanned image, no table rules")
        return doc.tobytes()
    finally:
        doc.close()


def _source(vlm, *, call_cap: int = 16) -> tuple[AnalysisSourceBuilder, PageSnapshot, DocumentSnapshot]:
    prior = _empty_kosa_page()
    page = PageSnapshot(
        page_no=1,
        extracted_text=prior,
        extraction_method="VLM",
        layout_json={"needs_vlm": False, "vlm_transcribed": True},
    )
    doc = DocumentSnapshot(
        id=uuid4(),
        original_filename="kosa-scanned-p1.pdf",
        extension="pdf",
        mime_type="application/pdf",
        storage_key="unused",
        preview_storage_key=None,
        document_type_code="DOC-KOSA",
        document_type_name="KOSA",
        version_no=1,
        person_id=uuid4(),
        pages=[page],
    )
    settings = Settings(
        _env_file=None,
        analysis_vlm_table_banding_enabled=True,
        analysis_vlm_table_banding_reprocess_done=False,
        analysis_vlm_table_banding_max_calls_total=call_cap,
    )
    source = AnalysisSourceBuilder(object(), settings=settings, vlm=vlm)
    source._open_render_source = (  # type: ignore[method-assign]
        lambda _snapshot: pymupdf.open(stream=_scan_pdf(), filetype="pdf")
    )
    return source, page, doc


class TileVLM:
    def __init__(self, quality_ok: bool, *, projects_ok: bool = True) -> None:
        self.calls = 0
        self.contexts: list[dict] = []
        self.quality_ok = quality_ok
        self.projects_ok = projects_ok

    def transcribe_image(
        self, *, image_bytes, mime_type="image/png", log_context=None, user_prompt=None
    ) -> str:
        assert image_bytes and mime_type == "image/png"
        assert user_prompt and "근무경력" in user_prompt
        self.calls += 1
        context = log_context or {}
        self.contexts.append(context)
        if context.get("kosa_work_table_crop"):
            if self.quality_ok:
                return "\n".join(
                    f"가상회사{i} | 200{i}.01.01 ~ 200{i}.12.31 | 개발"
                    for i in range(1, 8)
                )
            return "프로젝트 참여기간 | 2024.01.01 ~ 2024.06.01"
        if context.get("kosa_project_table_crop"):
            if not self.projects_ok:
                return "원본 프로젝트 행 전사 불가"
            return (
                "가상 제조 데이터 융합 | 2024.11.18 ~ 2025.01.10 | "
                "기관A | 수행회사 | 이사 | 컨설팅\n"
                "가상 재판 AI 설계 | 2024.05.09 ~ 2024.09.30 | "
                "기관B | 수행회사 | 이사 | 컨설팅"
            )
        if context.get("page_tile"):
            if self.quality_ok:
                return [
                    "근무경력\n회사명 | 재직기간\n에이회사 | 2001.01.01 ~ 2003.02.01",
                    "비이회사 | 2003.03.01 ~ 2005.06.01",
                    "씨이회사 | 2005.07.01 ~ 2008.12.31",
                ][int(context["page_tile"].split("/")[0]) - 1]
            return "추출할 수 없음"
        raise AssertionError("Unexpected VLM call")


def test_scanned_page_recovery_quality_gate() -> None:
    assert is_kosa_empty_markdown_table_vlm_quality_issue(_empty_kosa_page())
    assert is_plausible_kosa_employment_recovery(
        "근무경력\n에이회사 | 2001.01.01 ~ 2003.02.01\n"
        "비이회사 | 2003.03.01 ~ 2005.06.01"
    )
    assert not is_plausible_kosa_employment_recovery("회사명 | 1962.06.24")
    assert not is_plausible_kosa_employment_recovery(
        "기술경력\n2001.01.01 ~ 2003.02.01\n2003.03.01 ~ 2005.06.01"
    )
    assert not is_plausible_kosa_employment_recovery(_empty_kosa_page())
    assert not is_plausible_kosa_employment_recovery(
        "근무경력\n에이회사 2001.01.01 ~ 2003.02.01\n"
        "비이회사 2003.03.01 ~ 2005.06.01\n"
        "기술경력\n참여사업명 프로젝트 A\n"
        "2024.05.01 ~ 2024.06.30"
    )


@pytest.mark.parametrize("quality_ok", [True, False])
def test_scanned_kosa_without_ruling_recovered_in_memory_only(quality_ok) -> None:
    vlm = TileVLM(quality_ok)
    source, page, doc = _source(vlm)
    prior = page.extracted_text
    layout = dict(page.layout_json or {})

    result = source.build([doc])

    assert result.vlm_transcriptions == []
    assert page.extracted_text == prior
    assert page.layout_json == layout
    assert vlm.contexts[0].get("kosa_work_table_crop") is True
    if quality_ok:
        assert vlm.calls == 2
        assert result.blocks[0].text.startswith("[PAGE 1]\n근무경력\n")
        assert "가상회사1 | 2001.01.01" in result.blocks[0].text
        assert "가상회사7 | 2007.01.01" in result.blocks[0].text
        assert "\n기술경력\n" in result.blocks[0].text
        assert "가상 제조 데이터 융합" in result.blocks[0].text
        assert "가상 재판 AI 설계" in result.blocks[0].text
        assert vlm.contexts[1].get("kosa_project_table_crop") is True
        assert result.blocks[0].vlm_pages_used == 1
        assert result.blocks[0].vlm_table_band_calls_used == 2
    else:
        # Bad one-crop evidence causes a conservative fallback (three tiles).
        assert vlm.calls == 4
        assert [ctx.get("page_tile") for ctx in vlm.contexts[1:]] == [
            "1/3", "2/3", "3/3"
        ]
        assert result.blocks[0].text == f"[PAGE 1]\n{prior.strip()}"
        assert result.blocks[0].vlm_pages_used == 0


def test_scanned_kosa_tiles_respect_total_call_cap() -> None:
    vlm = FakeVLMProvider("근무경력\n에이회사 2001.01 ~ 2002.12")
    source, page, doc = _source(vlm, call_cap=2)

    result = source.build([doc])

    assert vlm.calls == 1  # Crop consumes one call; three-tile fallback stays capped.
    assert result.vlm_transcriptions == []
    assert result.blocks[0].text == f"[PAGE 1]\n{page.extracted_text.strip()}"



def test_kosa_work_crop_rejects_invalid_dates_and_project_rows() -> None:
    from app.modules.analysis.vlm_table_banding import (
        normalize_kosa_cropped_employment_rows,
    )

    good = "\n".join(
        f"가상회사{i} | 199{i}.02.01 ~ 199{i}.12.31 | 업무"
        for i in range(1, 8)
    )
    assert normalize_kosa_cropped_employment_rows(good) == (
        "근무경력\n" + good
    )
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2001.01.01 ~ 2003.02.31 | 개발\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2009.01.01 ~ 2001.02.01 | 개발\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "프로젝트 | 2023.01.01 ~ 2023.12.31 | 참여기간\n"
        "나회사 | 2004.01.01 ~ 2005.01.01 | 개발\n"
        "다회사 | 2006.01.01 ~ 2007.01.01 | 개발"
    ) is None
    assert normalize_kosa_cropped_employment_rows(
        "가상회사 | 2001.01.01 ~ 2003.02.01 | 업무"
    ) is None



def test_kosa_core_output_reserve_scoped_to_verified_p1() -> None:
    from app.modules.analysis.token_budget import (
        verified_kosa_core_output_ratio,
    )

    doc_id = "00000000-0000-0000-0000-000000000038"
    other_id = "00000000-0000-0000-0000-000000000039"
    p1 = "근무경력\n" + "\n".join(
        f"가상회사{i} | 200{i}.01.01 ~ 200{i}.12.31 | 개발"
        for i in range(1, 8)
    )
    texts = {
        (doc_id, 1): p1,
        (doc_id, 2): "기술경력\n프로젝트 A | 2020.01.01 ~ 2020.12.31",
    }
    def ratio(
        page_texts, types, thinking=False, configured=0.25,
    ) -> float:
        return verified_kosa_core_output_ratio(
            configured,
            page_texts=page_texts,
            document_type_by_id=types,
            enable_thinking=thinking,
        )

    kinds = {doc_id: "DOC-KOSA"}
    assert ratio(texts, kinds) == 7.0 / 16.0
    assert ratio(texts, kinds, configured=0.5) == 0.5
    assert ratio(texts, kinds, thinking=True) == 0.25
    assert ratio(texts, kinds, thinking=None) == 0.25
    assert ratio(texts, {doc_id: "DOC-RESUME"}) == 0.25
    assert ratio({(doc_id, 1): "근무경력\n| | |"}, kinds) == 0.25
    assert ratio({
        **texts, (other_id, 1): "other resume",
    }, {**kinds, other_id: "DOC-RESUME"}) == 0.25
    assert ratio({
        (doc_id, 1): p1 + "\n기술경력\n참여사업 2024.01.01",
    }, kinds) == 0.25



def test_kosa_p1_missing_lower_crop_fails_closed_without_losing_projects() -> None:
    vlm = TileVLM(True, projects_ok=False)
    source, page, doc = _source(vlm)
    result = source.build([doc])
    assert vlm.calls == 2
    assert result.blocks[0].text == f"[PAGE 1]\n{page.extracted_text.strip()}"
    assert result.blocks[0].vlm_pages_used == 0
    assert result.blocks[0].vlm_table_band_calls_used == 2
    assert result.vlm_transcriptions == []


def test_kosa_p1_one_call_budget_never_publishes_work_only_page() -> None:
    vlm = TileVLM(True)
    source, page, doc = _source(vlm, call_cap=1)
    result = source.build([doc])
    assert vlm.calls == 1
    assert result.blocks[0].text == f"[PAGE 1]\n{page.extracted_text.strip()}"
    assert result.blocks[0].vlm_table_band_calls_used == 1
    assert result.blocks[0].vlm_pages_used == 0


def test_kosa_p1_combined_work_project_validation_and_recall() -> None:
    from app.ai.schemas.profile_candidate import ProfileCandidateDocument
    from app.modules.analysis.kosa_project_recall import supplement_kosa_table_projects
    from app.modules.analysis.vlm_table_banding import (
        normalize_kosa_cropped_project_rows,
        split_verified_kosa_p1_sections,
    )
    from app.modules.analysis.token_budget import verified_kosa_core_output_ratio

    doc_id = "00000000-0000-0000-0000-000000000038"
    work = "근무경력\n" + "\n".join(
        f"가상회사{i} | 200{i}.01.01 ~ 200{i}.12.31 | 개발"
        for i in range(1, 8)
    )
    rows = (
        "제조 데이터 융합 | 2024.11.18 ~ 2025.01.10 | 기관A | 수행회사 | 이사 | 컨설팅\n"
        "재판 AI 설계 | 2024.05.09 ~ 2024.09.30 | 기관B | 수행회사 | 이사 | 컨설팅"
    )
    technical = normalize_kosa_cropped_project_rows(rows)
    assert technical == "기술경력\n" + rows
    combined = work + "\n" + technical
    assert split_verified_kosa_p1_sections(combined) == (work, technical)
    assert verified_kosa_core_output_ratio(
        0.25, page_texts={(doc_id, 1): combined},
        document_type_by_id={doc_id: "DOC-KOSA"}, enable_thinking=False,
    ) == 7 / 16

    base = ProfileCandidateDocument()
    result, metrics = supplement_kosa_table_projects(
        base, page_texts={(doc_id, 1): combined},
        document_type_by_id={doc_id: "DOC-KOSA"},
    )
    assert metrics == {"table_rows": 2, "matched_rows": 0, "supplemented_rows": 2}
    assert len(result.projects) == 2
    assert [p.project_name for p in result.projects] == [
        "제조 데이터 융합", "재판 AI 설계"
    ]
    assert all(
        ref.document_id == doc_id and ref.page_no == 1
        for project in result.projects for ref in project.source_refs
    )
    assert all(project.source_refs[0].quote_text in rows for project in result.projects)

    duplicate, metrics_again = supplement_kosa_table_projects(
        result, page_texts={(doc_id, 1): combined},
        document_type_by_id={doc_id: "DOC-KOSA"},
    )
    assert len(duplicate.projects) == 2
    assert metrics_again == {"table_rows": 2, "matched_rows": 2, "supplemented_rows": 0}

    invalid = (
        "제조 데이터 융합 | 2024.11.18 ~ 2025.01.10 | 기관A | 수행회사 | 이사 | 컨설팅\n"
        "재판 AI 설계 | 2024.05.09 ~ 2024.02.30 | 기관B | 수행회사 | 이사 | 컨설팅"
    )
    assert normalize_kosa_cropped_project_rows(invalid) is None
    assert split_verified_kosa_p1_sections(work + "\n기술경력\n" + invalid) is None
    unchanged, bad_metrics = supplement_kosa_table_projects(
        base, page_texts={(doc_id, 1): work + "\n기술경력\n" + invalid},
        document_type_by_id={doc_id: "DOC-KOSA"},
    )
    assert not unchanged.projects
    assert bad_metrics["table_rows"] == 0


def test_kosa_p1_project_only_rows_are_not_employers() -> None:
    from app.ai.schemas.profile_candidate import (
        EmploymentCandidate, ProfileCandidateDocument, SourceRef
    )
    from app.modules.analysis.candidate_quality_guards import (
        apply_candidate_quality_guards,
    )
    doc_id = "00000000-0000-0000-0000-000000000038"
    work = "근무경력\n" + "\n".join(
        f"가상회사{i} | 200{i}.01.01 ~ 200{i}.12.31 | 개발"
        for i in range(1, 8)
    )
    technical = (
        "기술경력\n"
        "제조 데이터 융합 | 2024.11.18 ~ 2025.01.10 | "
        "기관A | 수행회사 | 이사 | 컨설팅"
    )
    fake_employer = EmploymentCandidate(
        company_name="수행회사", start_date="2024.11.18",
        end_date="2025.01.10",
        source_refs=[SourceRef(
            document_id=doc_id, page_no=1,
            quote_text="수행회사",
        )],
    )
    real_employer = EmploymentCandidate(
        company_name="가상회사1", start_date="2001.01.01",
        end_date="2001.12.31",
        source_refs=[SourceRef(
            document_id=doc_id, page_no=1,
            quote_text="가상회사1 | 2001.01.01 ~ 2001.12.31 | 개발",
        )],
    )
    result = apply_candidate_quality_guards(
        ProfileCandidateDocument(employment_history=[real_employer, fake_employer]),
        page_texts={(doc_id, 1): work + "\n" + technical},
        document_type_by_id={doc_id: "DOC-KOSA"},
    )
    assert len(result.employment_history) == 1
    assert result.employment_history[0].company_name == "가상회사1"
