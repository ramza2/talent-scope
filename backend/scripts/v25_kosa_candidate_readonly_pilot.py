"""Read-only v25 Candidate pilot with in-memory KOSA row-banded transcription.

Preflight (default): inspect a stored run, verify four scanned-table bands,
and check that the original source still fits the prompt. No VLM/LLM calls.
Live (explicit opt-in): run four VLM band calls and v25 extraction in memory.
Never call run_analysis(), persist transcriptions, create diffs, or commit.
The script contains no case-specific names, identifiers, or credentials.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pymupdf
from sqlalchemy import text

from app.ai.prompts.profile_extract import resolve_profile_prompt
from app.ai.providers.llm import OpenAICompatibleLLMProvider
from app.ai.providers.vlm import OpenAICompatibleVLMProvider
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.service import AnalysisService, apply_candidate_quality_guards
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
)
from app.modules.analysis.vlm_table_banding import (
    detect_table_row_bands,
    is_valid_table_band_transcription,
)
from app.storage.s3 import get_object_storage


KOSA_PAGE = 3
EXPECTED_BANDS = ((1, 5), (6, 10), (11, 15), (16, 17))
PROMPT_VERSION = "profile-extract-v25"


def _sha(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _norm(value: str | None) -> str:
    return "".join(char.lower() for char in (value or "") if char.isalnum())


def _month(value: str | None) -> str | None:
    match = re.search(r"(\d{4})[.\-/](\d{1,2})", value or "")
    return f"{match.group(1)}-{int(match.group(2)):02d}" if match else None


@dataclass(frozen=True)
class Snapshot:
    documents: tuple[DocumentSnapshot, ...]
    codes: list
    aliases: dict
    base_snapshot: dict | None
    run_status: str
    candidate_sha: str
    kosa_document_id: UUID
    kosa_page_signature: tuple


def _read_snapshot(run_id: UUID) -> Snapshot:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        repo = AnalysisRepository(db)
        run = repo.get_run(run_id)
        assert run is not None, "ANALYSIS_RUN_NOT_FOUND"
        document_ids = repo.list_run_document_ids(run_id)
        rows = repo.list_documents_by_ids(document_ids)
        assert rows and len(rows) == len(document_ids), "RUN_DOCUMENTS_MISSING"

        snapshots = []
        for document, group, type_name in rows:
            assert group.person_id == run.person_id, "DOCUMENT_PERSON_MISMATCH"
            pages = repo.list_document_pages(document.id)
            snapshots.append(
                DocumentSnapshot(
                    id=document.id,
                    original_filename=document.original_filename,
                    extension=document.extension,
                    mime_type=document.mime_type,
                    storage_key=document.storage_key,
                    preview_storage_key=document.preview_storage_key,
                    document_type_code=group.document_type_code,
                    document_type_name=type_name,
                    version_no=document.version_no,
                    person_id=group.person_id,
                    pages=[
                        PageSnapshot(
                            page_no=page.page_no,
                            extracted_text=page.extracted_text,
                            layout_json=copy.deepcopy(page.layout_json),
                            extraction_method=page.extraction_method,
                        )
                        for page in pages
                    ],
                )
            )
        kosa = [
            doc for doc in snapshots
            if (doc.extension or "").casefold() == "pdf"
            and "kosa" in doc.original_filename.casefold()
        ]
        assert len(kosa) == 1, f"EXPECTED_ONE_KOSA_PDF: {len(kosa)}"
        kosa_page = next(
            (page for page in kosa[0].pages if page.page_no == KOSA_PAGE), None
        )
        assert kosa_page is not None, "KOSA_PAGE_3_MISSING"
        assert run.base_profile_version is not None, "BASE_REVISION_MISSING"
        base = repo.get_profile_revision_snapshot(
            run.person_id, run.base_profile_version
        )
        assert base is not None, "BASE_REVISION_NOT_FOUND"
        codes = repo.list_active_codes()
        aliases = repo.list_aliases_for_codes([code.code for code in codes])
        result = Snapshot(
            documents=tuple(snapshots),
            codes=codes,
            aliases=aliases,
            base_snapshot=base,
            run_status=run.status,
            candidate_sha=_sha(run.candidate_json),
            kosa_document_id=kosa[0].id,
            kosa_page_signature=(
                kosa_page.extracted_text,
                kosa_page.extraction_method,
                copy.deepcopy(kosa_page.layout_json),
            ),
        )
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        return result


def _check_unchanged(run_id: UUID, snapshot: Snapshot) -> None:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AnalysisRepository(db)
        run = repo.get_run(run_id)
        assert run is not None
        assert run.status == snapshot.run_status, "ANALYSIS_STATUS_CHANGED"
        assert _sha(run.candidate_json) == snapshot.candidate_sha, "CANDIDATE_CHANGED"
        page = next(
            p for p in repo.list_document_pages(snapshot.kosa_document_id)
            if p.page_no == KOSA_PAGE
        )
        assert (
            page.extracted_text,
            page.extraction_method,
            page.layout_json,
        ) == snapshot.kosa_page_signature, "KOSA_PAGE_CHANGED"
        assert db.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
    print("RUN_CANDIDATE_DOCUMENT_PAGE_UNCHANGED: PASS", flush=True)


class MeteredLLM(OpenAICompatibleLLMProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.json_calls = 0
        self.token_calls = 0

    def complete_json(self, **kwargs):
        self.json_calls += 1
        return super().complete_json(**kwargs)

    def count_prompt_tokens(self, **kwargs):
        self.token_calls += 1
        return super().count_prompt_tokens(**kwargs)


def _compare_kosa_rows(text_value: str, candidate, kosa_id: UUID) -> None:
    project_rows = list(candidate.projects)
    metrics = {"PASS": 0, "NEEDS_REVIEW": 0, "NOT_FOUND": 0}
    for index, line in enumerate(text_value.splitlines(), 1):
        cells = [cell.strip() for cell in line.split("|")]
        assert len(cells) == 7, f"INVALID_KOSA_ROW_{index}"
        title = cells[1]
        period = cells[2].split("~")
        assert len(period) == 2, f"INVALID_KOSA_PERIOD_{index}"
        same_title = [
            row for row in project_rows
            if _norm(row.project_name) == _norm(title)
        ]
        if not same_title:
            status = "NOT_FOUND"
            problems = ["project"]
        elif len(same_title) > 1:
            status = "NEEDS_REVIEW"
            problems = ["duplicate_title"]
        else:
            row = same_title[0]
            problems = []
            if _month(row.start_date) != _month(period[0]):
                problems.append("start_date")
            if _month(row.end_date) != _month(period[1]):
                problems.append("end_date")
            if _norm(row.customer_name) != _norm(cells[3]):
                problems.append("customer")
            if not any(
                ref.document_id == str(kosa_id) and ref.page_no == KOSA_PAGE
                for ref in row.source_refs
            ):
                problems.append("kosa_source_ref")
            status = "NEEDS_REVIEW" if problems else "PASS"
        metrics[status] += 1
        print(
            f"KOSA_ROW_{index:02d}: {status}"
            f" title={title[:90]!r} reason={','.join(problems) or '-'}",
            flush=True,
        )
    print("KOSA_ROW_METRICS:", metrics, flush=True)


def main() -> None:
    run_id = UUID(os.environ["TALENTSCOPE_V25_PILOT_RUN_ID"])
    mode = os.environ.get("TALENTSCOPE_V25_PILOT_MODE", "preflight").lower()
    assert mode in {"preflight", "live"}, "MODE_MUST_BE_PREFLIGHT_OR_LIVE"
    settings = get_settings()
    prompt = resolve_profile_prompt(PROMPT_VERSION)
    assert prompt.compact_protocol and prompt.guard_core_section_authority
    assert prompt.project_page_recall_split

    snapshot = _read_snapshot(run_id)
    storage = get_object_storage()
    no_vlm = settings.model_copy(update={"analysis_max_vlm_pages": 0})
    builder = AnalysisSourceBuilder(storage, settings=no_vlm, vlm=None)
    kosa = next(d for d in snapshot.documents if d.id == snapshot.kosa_document_id)
    page = next(p for p in kosa.pages if p.page_no == KOSA_PAGE)
    print("===== 1. V25 READ-ONLY SNAPSHOT =====", flush=True)
    print("DB_READ_ONLY: on", flush=True)
    print("PROMPT_VERSION:", prompt.prompt_version, flush=True)
    print("RUN_STATUS:", snapshot.run_status, flush=True)
    print("DOCUMENT_COUNT:", len(snapshot.documents), flush=True)
    print("PAGE_COUNTS:", [len(d.pages) for d in snapshot.documents], flush=True)
    print("ORIGINAL_KOSA_PAGE_CHARS:", len(page.extracted_text or ""), flush=True)

    started = time.monotonic()
    try:
        print("===== 2. SCANNED TABLE BAND PREFLIGHT =====", flush=True)
        pdf = builder._open_render_source(kosa)
        assert pdf is not None and len(pdf) >= KOSA_PAGE, "KOSA_PDF_UNAVAILABLE"
        try:
            bands = detect_table_row_bands(
                pdf[KOSA_PAGE - 1], dpi=220,
                rows_per_band=5, max_bands=6,
            )
            ranges = tuple((b.first_row, b.last_row) for b in bands)
            print("BANDS:", list(ranges), flush=True)
            assert ranges == EXPECTED_BANDS, "UNEXPECTED_KOSA_BANDS"
            if mode == "live":
                print("===== 3. VLM 4-BAND TRANSCRIPTION =====", flush=True)
                vlm_provider = OpenAICompatibleVLMProvider(settings)
                band_builder = AnalysisSourceBuilder(
                    storage, settings=settings, vlm=vlm_provider
                )
                transcribed, vlm_calls = band_builder._try_transcribe_table_bands(
                    pdf[KOSA_PAGE - 1],
                    log_context={
                        "pilot": "v25_candidate_read_only",
                        "document_id": str(kosa.id),
                        "page_no": KOSA_PAGE,
                    },
                    remaining_calls=6,
                )
                assert vlm_calls == 4 and transcribed, "KOSA_BANDING_FAILED"
                assert is_valid_table_band_transcription(
                    transcribed, expected_rows=17, expected_columns=7
                ), "KOSA_17_ROWS_7_COLUMNS_INVALID"
                page.extracted_text = transcribed  # detached in-memory snapshot ONLY
                print("VLM_CALLS:", vlm_calls, flush=True)
                print("KOSA_17_ROWS_7_COLUMNS: PASS", flush=True)
        finally:
            pdf.close()

        bundle = builder.build(
            list(snapshot.documents),
            log_context={"pilot": "v25_candidate_read_only"},
        )
        assert not bundle.vlm_transcriptions, "UNEXPECTED_VLM_WRITE_CANDIDATE"
        prompt_source = bundle.build_prompt_source(
            int(settings.analysis_max_total_text_chars)
        )
        blocks = prompt_source.text
        assert blocks.strip(), "EMPTY_ANALYSIS_SOURCE"
        kosa_key = (str(kosa.id), KOSA_PAGE)
        assert kosa_key in prompt_source.page_texts, "KOSA_PAGE_NOT_IN_PROMPT"
        if mode == "live":
            assert page.extracted_text.strip() in prompt_source.page_texts[kosa_key], (
                "KOSA_17_ROWS_TRUNCATED"
            )
        print("PROMPT_SOURCE_CHARS:", len(blocks), flush=True)
        print("PROMPT_PAGE_REFS:", len(prompt_source.page_texts), flush=True)
        if mode == "preflight":
            print("===== V25_KOSA_READ_ONLY_PREFLIGHT_PASS =====", flush=True)
            return

        print("===== 4. V25 IN-MEMORY LLM EXTRACTION =====", flush=True)
        llm = MeteredLLM(settings)
        service = AnalysisService(
            None, storage=storage, settings=settings, llm=llm
        )
        # The extraction method only asks its repository for these two
        # catalogs. Replace it with an object with no database write methods.
        service.repo = SimpleNamespace(
            list_active_codes=lambda: snapshot.codes,
            list_aliases_for_codes=lambda _codes: snapshot.aliases,
        )
        catalog_map = {
            code.code: (code.code_type, bool(code.is_active))
            for code in snapshot.codes
        }
        base_log = {
            "analysis_run_id": str(run_id),
            "pilot": "v25_candidate_read_only",
            "document_count": len(snapshot.documents),
            "vlm_pages": 1,
            "prompt_version": prompt.prompt_version,
            "source_char_count": len(blocks.strip()),
        }
        raw = service._extract_staged_compact_candidate(
            prompt=prompt,
            document_blocks=blocks,
            catalog_map=catalog_map,
            allowed_documents=prompt_source.allowed_documents,
            page_texts=prompt_source.page_texts,
            documents=snapshot.documents,
            source_char_count=len(blocks.strip()),
            base_log=base_log,
            vlm_pages=1,
        )
        base_profile = snapshot.base_snapshot.get("profile") or {}
        confirmed_name = base_profile.get("name") if isinstance(base_profile, dict) else None
        doc_type_by_id = {
            str(doc.id): str(doc.document_type_code)
            for doc in snapshot.documents if doc.document_type_code
        }
        guarded = apply_candidate_quality_guards(
            raw,
            confirmed_person_name=confirmed_name,
            page_texts=prompt_source.page_texts,
            document_type_by_id=doc_type_by_id,
            strict_section_authority=prompt.guard_core_section_authority,
        )
        print("PROJECTS_RAW:", len(raw.projects), flush=True)
        print("PROJECTS_GUARDED:", len(guarded.projects), flush=True)
        print("EMPLOYMENT_GUARDED:", len(guarded.employment_history), flush=True)
        print("EDUCATION_GUARDED:", len(guarded.education), flush=True)
        print("CERTIFICATIONS_GUARDED:", len(guarded.certifications), flush=True)
        reference = os.environ.get("TALENTSCOPE_V25_REFERENCE_PROJECTS", "").strip()
        if reference:
            print("PREVIOUS_PILOT_REFERENCE:", int(reference), flush=True)
            print("PROJECT_COUNT_DELTA:", len(guarded.projects) - int(reference), flush=True)
        _compare_kosa_rows(page.extracted_text or "", guarded, kosa.id)
        print("LLM_COMPLETE_JSON_CALLS:", llm.json_calls, flush=True)
        print("LLM_TOKEN_COUNT_CALLS:", llm.token_calls, flush=True)
        print("ELAPSED_SECONDS:", round(time.monotonic() - started, 1), flush=True)
        print("===== V25_KOSA_CANDIDATE_READ_ONLY_LIVE_COMPLETE =====", flush=True)
    finally:
        _check_unchanged(run_id, snapshot)


if __name__ == "__main__":
    main()
