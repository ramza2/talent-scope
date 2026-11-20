"""V38 KOSA opt-in, read-only real-input integration probe.

Run only against an explicitly designated REVIEWING analysis. This program
never claims an analysis, writes transcriptions, generates DB records, or
commits. The isolated API invocation must mount the V38 backend read-only.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text

from app.ai.prompts.profile_extract import resolve_profile_prompt
from app.ai.providers.errors import AIProviderError, AIResponseTruncatedError
from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.ai.providers.vlm import OpenAICompatibleVLMProvider
from app.ai.schemas.profile_candidate import ProfileCandidateDocument
from app.core.config import get_settings
from app.db.models.analysis import AnalysisRun, AnalysisRunDocument
from app.db.models.document import Document, DocumentGroup, DocumentPage
from app.db.session import SessionLocal
from app.modules.analysis.candidate_quality_guards import apply_candidate_quality_guards
from app.modules.analysis.diff_engine import build_diffs
from app.modules.analysis.service import AnalysisService
from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot
from app.modules.analysis.vlm_table_banding import split_verified_kosa_p1_sections

EXPECTED_PERIODS = {
    ("2008.06.24", "2009.06.25"),
    ("2007.03.12", "2008.02.28"),
    ("2006.03.04", "2006.08.30"),
    ("2004.08.01", "2005.04.29"),
    ("2001.06.01", "2003.12.31"),
    ("1999.10.01", "2001.05.30"),
    ("1990.05.07", "1999.09.30"),
}
EXPECTED_P1_PROJECTS = {
    "AI 제조 데이터 융합 컨설팅",
    "재판업무 지원 AI 모델 설계",
}


def project_key(project) -> tuple[str, str, str]:
    return (
        (project.project_name or "").strip(),
        (project.start_date or "").replace("-", "."),
        (project.end_date or "").replace("-", "."),
    )


def period(row) -> tuple[str, str]:
    return (
        (row.start_date or "").replace("-", ".").replace("/", "."),
        (row.end_date or "").replace("-", ".").replace("/", "."),
    )


class LimitedVLM:
    def __init__(self, settings):
        self.inner = OpenAICompatibleVLMProvider(settings)
        self.calls = 0
        self.contexts = []

    def transcribe_image(self, **kwargs):
        if self.calls >= 2:
            raise AIProviderError("V38_VLM_CALL_LIMIT")
        self.calls += 1
        context = kwargs.get("log_context") or {}
        self.contexts.append(context)
        print("VLM_CALL=", self.calls,
              "CROP=", "work" if context.get("kosa_work_table_crop")
              else "project" if context.get("kosa_project_table_crop")
              else "other", flush=True)
        return self.inner.transcribe_image(**kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class LimitedLLM:
    def __init__(self, settings):
        self.inner = OpenAICompatibleLLMProvider(settings)
        self.calls = 0
        self.phase_counts = Counter()
        self.truncations = 0
        self.token_calls = 0

    def count_prompt_tokens(self, **kwargs):
        if self.token_calls >= 50:
            raise AIProviderError("V38_TOKEN_COUNT_LIMIT")
        self.token_calls += 1
        return self.inner.count_prompt_tokens(**kwargs)

    def complete_json(self, **kwargs):
        if self.calls >= 12:
            raise AIProviderError("V38_LLM_CALL_LIMIT")
        phase = (kwargs.get("log_context") or {}).get("phase")
        if phase not in {"core", "projects"}:
            raise AssertionError("V38_UNEXPECTED_LLM_PHASE")
        self.calls += 1
        self.phase_counts[phase] += 1
        print("LLM_CALL=", self.calls, "PHASE=", phase,
              "OUTPUT_LIMIT=", kwargs.get("max_completion_tokens"), flush=True)
        try:
            return self.inner.complete_json(**kwargs)
        except AIResponseTruncatedError:
            self.truncations += 1
            print("TRUNCATED=", self.calls, flush=True)
            raise

    def __getattr__(self, name):
        return getattr(self.inner, name)


def run(run_id: UUID, document_id: UUID) -> None:
    settings = get_settings().model_copy(update={
        "analysis_vlm_table_banding_enabled": True,
        "analysis_vlm_table_banding_reprocess_done": False,
        "analysis_vlm_table_banding_max_calls_total": 2,
        "analysis_vlm_table_banding_max_calls_per_page": 2,
    })
    llm = LimitedLLM(settings)
    vlm = LimitedVLM(settings)
    doc_id = str(document_id)

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
        service = AnalysisService(db, settings=settings, llm=llm, vlm=vlm)
        try:
            analysis = db.get(AnalysisRun, run_id)
            assert analysis is not None and analysis.status == "REVIEWING"
            assert analysis.prompt_version == "profile-extract-v25"
            linked = db.execute(
                select(AnalysisRunDocument.document_id)
                .where(AnalysisRunDocument.analysis_run_id == run_id)
            ).scalars().all()
            assert linked == [document_id]
            document = db.get(Document, document_id)
            assert document is not None
            group = db.get(DocumentGroup, document.document_group_id)
            assert group is not None and group.document_type_code == "DOC-KOSA"
            pages = db.execute(
                select(DocumentPage).where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_no)
            ).scalars().all()
            assert [p.page_no for p in pages] == [1, 2, 3]
            existing = ProfileCandidateDocument.model_validate(
                analysis.candidate_json or {}
            )
            assert len(existing.projects) == 24
            base = service.repo.get_profile_revision_snapshot(
                analysis.person_id, analysis.base_profile_version
            )
            assert base is not None

            snapshot = DocumentSnapshot(
                id=document_id,
                original_filename=document.original_filename,
                extension=document.extension,
                mime_type=document.mime_type,
                storage_key=document.storage_key,
                preview_storage_key=document.preview_storage_key,
                document_type_code="DOC-KOSA",
                document_type_name=None,
                version_no=document.version_no,
                person_id=analysis.person_id,
                pages=[
                    PageSnapshot(
                        page_no=p.page_no,
                        extracted_text=p.extracted_text,
                        extraction_method=p.extraction_method,
                        layout_json=p.layout_json,
                    ) for p in pages
                ],
            )

            # Build + extract in memory; never call run_analysis() or persist helpers.
            bundle = service.source_builder.build(
                [snapshot], log_context={"diagnostic": "V38_READONLY_INTEGRATION"}
            )
            source = bundle.build_prompt_source(
                settings.analysis_max_total_text_chars
            )
            assert set(source.page_texts) == {
                (doc_id, 1), (doc_id, 2), (doc_id, 3)
            }
            assert vlm.calls == 2
            assert vlm.contexts[0].get("kosa_work_table_crop")
            assert vlm.contexts[1].get("kosa_project_table_crop")
            assert bundle.vlm_transcriptions == []

            sections = split_verified_kosa_p1_sections(
                source.page_texts[(doc_id, 1)]
            )
            assert sections is not None and sections[1]
            work_rows = sections[0].splitlines()[1:]
            technical_rows = sections[1].splitlines()[1:]
            assert len(work_rows) == 7
            assert len(technical_rows) == 2
            periods = set()
            for line in work_rows:
                cells = [value.strip() for value in line.split("|")]
                dates = [v.strip().replace("-", ".")
                         for v in cells[1].split("~")]
                periods.add(tuple(dates))
            assert periods == EXPECTED_PERIODS

            prompt = resolve_profile_prompt(analysis.prompt_version)
            codes = service.repo.list_active_codes()
            catalog_map = {
                code.code: (code.code_type, code.is_active)
                for code in codes
            }
            document_type_by_id = {doc_id: "DOC-KOSA"}
            common = dict(
                prompt=prompt,
                document_blocks=source.text,
                catalog_map=catalog_map,
                allowed_documents=source.allowed_documents,
                page_texts=source.page_texts,
                documents=[snapshot],
                source_char_count=len(source.text.strip()),
                base_log={
                    "analysis_run_id": "V38_READONLY_INTEGRATION",
                    "document_count": 1,
                    "source_char_count": len(source.text.strip()),
                },
                vlm_pages=bundle.total_vlm_pages,
            )
            print("PAGES=3 WORK_ROWS=7 TECHNICAL_ROWS=2", flush=True)
            core = service._extract_staged_compact_candidate(
                **common, skip_projects=True
            )
            guarded_core = apply_candidate_quality_guards(
                core,
                page_texts=source.page_texts,
                document_type_by_id=document_type_by_id,
                strict_section_authority=prompt.guard_core_section_authority,
            )
            assert len(guarded_core.employment_history) == 7
            assert {period(r) for r in guarded_core.employment_history} == EXPECTED_PERIODS
            print("CORE_GUARDED_EMPLOYMENT=7", flush=True)
            extracted = service._extract_staged_compact_candidate(
                **common,
                precomputed_core=guarded_core.model_copy(update={"projects": []}),
            )
            guarded = apply_candidate_quality_guards(
                extracted,
                page_texts=source.page_texts,
                document_type_by_id=document_type_by_id,
                strict_section_authority=prompt.guard_core_section_authority,
            )

            old_keys = {project_key(p) for p in existing.projects}
            new_keys = {project_key(p) for p in guarded.projects}
            present_names = {p.project_name for p in guarded.projects}
            missing = old_keys - new_keys
            added = new_keys - old_keys
            specs = build_diffs(guarded, base)
            print("===== V38 REAL INTEGRATION RESULTS =====")
            print("VLM_CALLS=", vlm.calls)
            print("CORE_LLM_CALLS=", llm.phase_counts["core"])
            print("PROJECT_LLM_CALLS=", llm.phase_counts["projects"])
            print("LLM_TRUNCATIONS=", llm.truncations)
            print("TOKEN_COUNT_CALLS=", llm.token_calls)
            print("EMPLOYMENT=", len(guarded.employment_history))
            print("PROJECTS=", len(guarded.projects))
            print("OLD_PROJECTS=", len(existing.projects))
            print("MISSING_OLD=", len(missing))
            print("NEW_IDENTITIES=", len(added))
            print("DIFF_TOTAL=", len(specs))
            for (entity, change), count in sorted(
                Counter((d.entity_type, d.change_type) for d in specs).items()
            ):
                print("DIFF=", entity, change, count)
            for name, start, end in sorted(missing):
                print("MISSING_OLD_PROJECT=", name, start, end)
            for name, start, end in sorted(added):
                print("NEW_PROJECT=", name, start, end)

            assert len(guarded.employment_history) == 7
            assert len(guarded.projects) == 26, "PROJECT_COUNT_NOT_26"
            assert not missing, "EXISTING_PROJECT_LOST"
            assert EXPECTED_P1_PROJECTS <= present_names, "P1_PROJECT_MISSING"
            assert not any(
                d.change_type == "REMOVE_CANDIDATE" for d in specs
            )
            print("TALENTSCOPE_V38_REAL_INTEGRATION_READONLY_PASS")
        finally:
            db.rollback()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    args = parser.parse_args()
    run(args.run_id, args.document_id)
