"""Build LLM document blocks from READY DocumentPage text (+ optional VLM)."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pymupdf

from app.ai.providers.errors import AIProviderError
from app.ai.providers.vlm import VLMProvider
from app.ai.prompts.vlm_transcribe_table_v2 import VLM_TABLE_BAND_USER
from app.core.config import Settings, get_settings
from app.modules.analysis.vlm_table_banding import (
    detect_table_row_bands,
    is_kosa_empty_markdown_table_vlm_quality_issue,
    is_plausible_kosa_employment_recovery,
    normalize_kosa_cropped_employment_rows,
    normalize_kosa_cropped_project_rows,
    split_verified_kosa_p1_sections,
    is_valid_table_band_transcription,
    normalize_table_band_transcription,
)
from app.modules.document_processing.types import (
    IMAGE_EXTENSIONS,
    MIN_TEXT_CHARS_FOR_READY_PAGE,
)
from app.storage.base import ObjectStorage

logger = logging.getLogger(__name__)

_VLM_DONE_METHODS = frozenset({"VLM", "HYBRID"})


@dataclass
class PageSnapshot:
    page_no: int
    extracted_text: str | None
    layout_json: dict[str, Any] | None
    extraction_method: str | None = None


@dataclass
class DocumentSnapshot:
    """Detached document metadata — no ORM outside short TX."""

    id: UUID
    original_filename: str
    extension: str | None
    mime_type: str | None
    storage_key: str
    preview_storage_key: str | None
    document_type_code: str | None
    document_type_name: str | None
    version_no: int
    person_id: UUID
    pages: list[PageSnapshot] = field(default_factory=list)


@dataclass(frozen=True)
class VLMPageTranscription:
    """Successful page-level VLM result to persist (same text used in the prompt)."""

    document_id: UUID
    page_no: int
    expected_extracted_text: str | None
    expected_extraction_method: str | None
    expected_layout_json: dict[str, Any] | None
    persisted_text: str
    extraction_method: str
    layout_json: dict[str, Any]


@dataclass
class DocumentSourceBlock:
    document_id: str
    filename: str
    document_type: str | None
    text: str = ""
    error: str | None = None
    vlm_pages_used: int = 0
    page_count: int = 0
    vlm_table_band_calls_used: int = 0


@dataclass
class PromptSource:
    """Text actually sent to the LLM, plus page scope for source_ref validation."""

    text: str
    allowed_documents: dict[str, set[int]] = field(default_factory=dict)
    page_texts: dict[tuple[str, int], str] = field(default_factory=dict)


def _parse_page_segments(body: str) -> list[tuple[int, str]]:
    """Split DocumentSourceBlock body into ``(page_no, page_text)`` segments."""
    segments: list[tuple[int, str]] = []
    current_page: int | None = None
    buf: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("[PAGE ") and line.endswith("]"):
            if current_page is not None:
                segments.append((current_page, "\n".join(buf).strip()))
            buf = []
            try:
                current_page = int(line[6:-1].strip())
            except ValueError:
                current_page = None
            continue
        if current_page is not None:
            buf.append(line)
    if current_page is not None:
        segments.append((current_page, "\n".join(buf).strip()))
    return segments


def _layout_dict(layout_json: dict[str, Any] | None) -> dict[str, Any]:
    return dict(layout_json) if isinstance(layout_json, dict) else {}


def _already_vlm_transcribed(
    *,
    extraction_method: str | None,
    layout_json: dict[str, Any] | None,
) -> bool:
    layout = _layout_dict(layout_json)
    method = (extraction_method or "").strip().upper()
    return method in _VLM_DONE_METHODS or bool(layout.get("vlm_transcribed"))


def _page_needs_vlm(
    *,
    text: str,
    extraction_method: str | None,
    layout_json: dict[str, Any] | None,
) -> bool:
    if _already_vlm_transcribed(
        extraction_method=extraction_method, layout_json=layout_json
    ):
        return False
    layout = _layout_dict(layout_json)
    return bool(layout.get("needs_vlm")) or len(text) < MIN_TEXT_CHARS_FOR_READY_PAGE


def _build_vlm_transcription(
    *,
    document_id: UUID,
    page: PageSnapshot,
    transcribed: str,
    table_banded: bool = False,
) -> VLMPageTranscription | None:
    cleaned = (transcribed or "").strip()
    if not cleaned:
        return None
    original = (page.extracted_text or "").strip()
    if original:
        persisted = f"{original}\n{cleaned}".strip()
        method = "HYBRID"
    else:
        persisted = cleaned
        method = "VLM"
    layout = _layout_dict(page.layout_json)
    layout["needs_vlm"] = False
    layout["vlm_transcribed"] = True
    layout["vlm_text_chars"] = len(cleaned)
    if table_banded:
        layout["vlm_table_banded"] = True
    layout["final_text_chars"] = len(persisted)
    expected_layout = (
        copy.deepcopy(page.layout_json) if isinstance(page.layout_json, dict) else None
    )
    return VLMPageTranscription(
        document_id=document_id,
        page_no=page.page_no,
        expected_extracted_text=page.extracted_text,
        expected_extraction_method=page.extraction_method,
        expected_layout_json=expected_layout,
        persisted_text=persisted,
        extraction_method=method,
        layout_json=layout,
    )


@dataclass
class AnalysisSourceBundle:
    blocks: list[DocumentSourceBlock] = field(default_factory=list)
    total_vlm_pages: int = 0
    vlm_transcriptions: list[VLMPageTranscription] = field(default_factory=list)

    @property
    def usable_blocks(self) -> list[DocumentSourceBlock]:
        return [b for b in self.blocks if (b.text or "").strip()]

    def combined_document_blocks(self, max_chars: int) -> str:
        """Build ``[DOCUMENT]`` / ``[PAGE n]`` blocks with a hard char cap."""
        return self.build_prompt_source(max_chars).text

    def build_prompt_source(self, max_chars: int) -> PromptSource:
        """Build LLM document text and the exact page scope included in it."""
        if max_chars <= 0:
            return PromptSource(text="")

        parts: list[str] = []
        remaining = max_chars
        join_sep = "\n\n"
        allowed: dict[str, set[int]] = {}
        page_texts: dict[tuple[str, int], str] = {}

        for src in self.usable_blocks:
            if remaining <= 0:
                break
            sep_cost = len(join_sep) if parts else 0
            if remaining <= sep_cost:
                break
            budget = remaining - sep_cost

            header = (
                "[DOCUMENT]\n"
                f"document_id: {src.document_id}\n"
                f"filename: {src.filename}\n"
                f"document_type: {src.document_type or ''}\n"
                "content:\n"
            )
            footer = "\n"
            overhead = len(header) + len(footer)
            if overhead >= budget:
                chunk = (header + footer)[:budget]
                if chunk:
                    parts.append(chunk)
                    remaining -= sep_cost + len(chunk)
                break

            body_budget = budget - overhead
            page_parts: list[str] = []
            used_body = 0
            segments = _parse_page_segments((src.text or "").strip())
            if not segments:
                # No page markers — include truncated whole body without page refs.
                body = (src.text or "").strip()
                if len(body) > body_budget:
                    body = body[:body_budget]
                block = f"{header}{body}{footer}"
                parts.append(block)
                remaining -= sep_cost + len(block)
                continue

            for page_no, page_text in segments:
                page_header = f"[PAGE {page_no}]\n"
                sep = "\n\n" if page_parts else ""
                available = body_budget - used_body - len(sep) - len(page_header)
                if available <= 0:
                    break
                included = page_text
                truncated = False
                if len(included) > available:
                    included = included[:available]
                    truncated = True
                if not included and not page_text:
                    # Empty page still marks presence if header fits.
                    pass
                chunk = f"{sep}{page_header}{included}"
                page_parts.append(chunk)
                used_body += len(chunk)
                allowed.setdefault(src.document_id, set()).add(page_no)
                page_texts[(src.document_id, page_no)] = included
                if truncated:
                    break

            body = "".join(page_parts)
            block = f"{header}{body}{footer}"
            parts.append(block)
            remaining -= sep_cost + len(block)

        result = join_sep.join(parts)
        if len(result) > max_chars:
            result = result[:max_chars]
        return PromptSource(
            text=result,
            allowed_documents=allowed,
            page_texts=page_texts,
        )


class AnalysisSourceBuilder:
    """DocumentPage text (+ VLM for needs_vlm pages). Does not create Evidence rows."""

    def __init__(
        self,
        storage: ObjectStorage,
        *,
        settings: Settings | None = None,
        vlm: VLMProvider | None = None,
    ) -> None:
        self.storage = storage
        self.settings = settings or get_settings()
        self.vlm = vlm

    def build(
        self,
        documents: list[DocumentSnapshot],
        *,
        log_context: dict | None = None,
    ) -> AnalysisSourceBundle:
        bundle = AnalysisSourceBundle()
        vlm_budget = int(self.settings.analysis_max_vlm_pages)
        table_budget = max(0, int(self.settings.analysis_vlm_table_banding_max_calls_total))
        for doc in documents:
            block, transcriptions = self._build_one(
                doc,
                vlm_budget=vlm_budget,
                table_budget=table_budget,
                log_context=log_context,
            )
            bundle.blocks.append(block)
            bundle.vlm_transcriptions.extend(transcriptions)
            bundle.total_vlm_pages += block.vlm_pages_used
            vlm_budget = max(0, vlm_budget - block.vlm_pages_used)
            table_budget = max(0, table_budget - block.vlm_table_band_calls_used)
        return bundle

    def _build_one(
        self,
        doc: DocumentSnapshot,
        *,
        vlm_budget: int,
        table_budget: int,
        log_context: dict | None,
    ) -> tuple[DocumentSourceBlock, list[VLMPageTranscription]]:
        doc_id = str(doc.id)
        max_pages = int(self.settings.analysis_max_pages_per_document)
        pages = list(doc.pages)[:max_pages]
        transcriptions: list[VLMPageTranscription] = []
        try:
            parts: list[str] = []
            vlm_used = 0
            table_calls_used = 0
            pdf_doc: pymupdf.Document | None = None
            try:
                for page in pages:
                    text = (page.extracted_text or "").strip()
                    needs_vlm = _page_needs_vlm(
                        text=text,
                        extraction_method=page.extraction_method,
                        layout_json=page.layout_json,
                    )

                    # A completed VLM page normally stays untouched. Opt-in
                    # table banding may refresh text in memory only (never
                    # mutate stored DocumentPage): explicit reprocess flag, or
                    # DOC-KOSA empty Markdown-table quality failure.
                    is_kosa = (doc.document_type_code or "").strip().upper() == "DOC-KOSA"
                    kosa_empty_table = is_kosa and is_kosa_empty_markdown_table_vlm_quality_issue(
                        text
                    )
                    if (
                        self.settings.analysis_vlm_table_banding_enabled
                        and (
                            self.settings.analysis_vlm_table_banding_reprocess_done
                            or kosa_empty_table
                        )
                        and not needs_vlm
                        and _already_vlm_transcribed(
                            extraction_method=page.extraction_method,
                            layout_json=page.layout_json,
                        )
                        and not _layout_dict(page.layout_json).get("vlm_table_banded")
                        and (doc.extension or "").lower() == "pdf"
                        and self.vlm is not None
                        and vlm_used < vlm_budget
                        and table_budget > table_calls_used
                    ):
                        if pdf_doc is None:
                            pdf_doc = self._open_render_source(doc)
                        if pdf_doc is not None and 0 < page.page_no <= len(pdf_doc):
                            banded_text, calls = self._try_transcribe_table_bands(
                                pdf_doc.load_page(page.page_no - 1),
                                log_context={
                                    **(log_context or {}),
                                    "document_id": doc_id,
                                    "page_no": page.page_no,
                                    "reprocess_done": bool(
                                        self.settings.analysis_vlm_table_banding_reprocess_done
                                    ),
                                    "kosa_empty_table": kosa_empty_table,
                                },
                                remaining_calls=table_budget - table_calls_used,
                            )
                            table_calls_used += calls
                            # An empty KOSA employment page must not be replaced
                            # with another empty table or a project-only answer.
                            if kosa_empty_table and banded_text is not None:
                                if not is_plausible_kosa_employment_recovery(banded_text):
                                    banded_text = None
                                elif page.page_no == 1:
                                    # A p1 employer-only table is not enough:
                                    # the same physical page can contain projects.
                                    sections = split_verified_kosa_p1_sections(banded_text)
                                    if sections is None or not sections[1]:
                                        banded_text = None
                            if kosa_empty_table and banded_text is None:
                                # KOSA p1 work-table crop is validated first.
                                # The observed scan has no detectable ruling;
                                # this narrow crop excludes lower project rows.
                                remaining = min(
                                    table_budget - table_calls_used,
                                    max(
                                        0,
                                        int(self.settings.analysis_vlm_table_banding_max_calls_per_page)
                                        - calls,
                                    ),
                                )
                                crop_calls = 0
                                suppress_tiles = False
                                if page.page_no == 1:
                                    banded_text, crop_calls = (
                                        self._try_transcribe_kosa_work_table_crop(
                                            pdf_doc.load_page(page.page_no - 1),
                                            log_context={
                                                **(log_context or {}),
                                                "document_id": doc_id,
                                                "page_no": page.page_no,
                                                "kosa_work_table_crop": True,
                                            },
                                            remaining_calls=remaining,
                                        )
                                    )
                                    table_calls_used += crop_calls
                                    if banded_text is not None:
                                        # A real KOSA p1 has an independent
                                        # lower 기술경력 table. Never replace p1
                                        # with employment-only text, even if
                                        # the work crop itself was valid.
                                        lower_remaining = min(
                                            table_budget - table_calls_used,
                                            max(
                                                0,
                                                int(self.settings.analysis_vlm_table_banding_max_calls_per_page)
                                                - calls - crop_calls,
                                            ),
                                        )
                                        projects, project_calls = (
                                            self._try_transcribe_kosa_project_table_crop(
                                                pdf_doc.load_page(page.page_no - 1),
                                                log_context={
                                                    **(log_context or {}),
                                                    "document_id": doc_id,
                                                    "page_no": page.page_no,
                                                    "kosa_project_table_crop": True,
                                                },
                                                remaining_calls=lower_remaining,
                                            )
                                        )
                                        table_calls_used += project_calls
                                        if projects is not None:
                                            banded_text += "\n" + projects
                                        else:
                                            banded_text = None
                                            suppress_tiles = True
                                if banded_text is None and not suppress_tiles:
                                    # Conservative fallback to the existing
                                    # tiled transcription on crop failure.
                                    remaining = min(
                                        table_budget - table_calls_used,
                                        max(
                                            0,
                                            int(self.settings.analysis_vlm_table_banding_max_calls_per_page)
                                            - calls - crop_calls,
                                        ),
                                    )
                                    banded_text, tile_calls = (
                                        self._try_transcribe_kosa_empty_page_tiles(
                                            pdf_doc.load_page(page.page_no - 1),
                                            log_context={
                                                **(log_context or {}),
                                                "document_id": doc_id,
                                                "page_no": page.page_no,
                                                "kosa_empty_page_tiles": True,
                                            },
                                            remaining_calls=remaining,
                                        )
                                    )
                                    table_calls_used += tile_calls
                            if banded_text is not None:
                                # Analysis-only replacement; never persist or
                                # rewrite the original DocumentPage text.
                                text = banded_text
                                vlm_used += 1
                            # Fail closed: keep stored extracted_text as-is.

                    if needs_vlm and self.vlm is not None and vlm_used < vlm_budget:
                        if pdf_doc is None:
                            pdf_doc = self._open_render_source(doc)
                        if pdf_doc is not None:
                            page_context = {
                                **(log_context or {}),
                                "document_id": doc_id,
                                "page_no": page.page_no,
                            }
                            banded_text = None
                            if (
                                self.settings.analysis_vlm_table_banding_enabled
                                and table_budget > table_calls_used
                            ):
                                banded_text, calls = self._try_transcribe_table_bands(
                                    pdf_doc.load_page(page.page_no - 1),
                                    log_context=page_context,
                                    remaining_calls=table_budget - table_calls_used,
                                )
                                table_calls_used += calls
                            if banded_text is None:
                                image_bytes = self._render_page_png(
                                    pdf_doc, page.page_no - 1
                                )
                                transcribed = self.vlm.transcribe_image(
                                    image_bytes=image_bytes,
                                    mime_type="image/png",
                                    log_context=page_context,
                                )
                            else:
                                transcribed = banded_text
                            vlm_used += 1
                            item = _build_vlm_transcription(
                                document_id=doc.id,
                                page=page,
                                transcribed=transcribed,
                                table_banded=banded_text is not None,
                            )
                            if item is not None:
                                transcriptions.append(item)
                                text = item.persisted_text
                        elif self._is_image(doc) and vlm_used < vlm_budget:
                            image_bytes = self._download(doc.storage_key)
                            mime = (
                                "image/png"
                                if (doc.extension or "").lower() == "png"
                                else "image/jpeg"
                            )
                            transcribed = self.vlm.transcribe_image(
                                image_bytes=image_bytes,
                                mime_type=mime,
                                log_context={
                                    **(log_context or {}),
                                    "document_id": doc_id,
                                    "page_no": page.page_no,
                                },
                            )
                            vlm_used += 1
                            item = _build_vlm_transcription(
                                document_id=doc.id,
                                page=page,
                                transcribed=transcribed,
                            )
                            if item is not None:
                                transcriptions.append(item)
                                text = item.persisted_text

                    if text:
                        parts.append(f"[PAGE {page.page_no}]\n{text}")
            finally:
                if pdf_doc is not None:
                    pdf_doc.close()

            return (
                DocumentSourceBlock(
                    document_id=doc_id,
                    filename=doc.original_filename,
                    document_type=doc.document_type_code,
                    text="\n\n".join(parts),
                    page_count=len(pages),
                    vlm_pages_used=vlm_used,
                    vlm_table_band_calls_used=table_calls_used,
                ),
                transcriptions,
            )
        except (AIProviderError, Exception) as exc:
            logger.info(
                "analysis source build failure document_id=%s err=%s",
                doc_id,
                type(exc).__name__,
            )
            return (
                DocumentSourceBlock(
                    document_id=doc_id,
                    filename=doc.original_filename,
                    document_type=doc.document_type_code,
                    error=f"{type(exc).__name__}: {str(exc)[:400]}",
                    page_count=len(pages),
                ),
                [],
            )

    def _try_transcribe_table_bands(
        self,
        page: pymupdf.Page,
        *,
        log_context: dict,
        remaining_calls: int,
    ) -> tuple[str | None, int]:
        """Fail closed to the existing full-page VLM path on any uncertainty."""
        if self.vlm is None:
            return None, 0
        per_page_cap = min(
            remaining_calls,
            max(0, int(self.settings.analysis_vlm_table_banding_max_calls_per_page)),
        )
        if per_page_cap < 2:
            return None, 0
        calls = 0
        try:
            dpi = max(220, int(self.settings.analysis_pdf_render_dpi))
            bands = detect_table_row_bands(
                page,
                dpi=dpi,
                rows_per_band=int(self.settings.analysis_vlm_table_banding_rows_per_band),
                max_bands=per_page_cap,
            )
            if not bands:
                return None, 0
            outputs: list[str] = []
            column_count: int | None = None
            for band in bands:
                zoom = min(max(dpi, 120), 240) / 72.0
                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(zoom, zoom),
                    clip=band.clip,
                    alpha=False,
                )
                calls += 1
                result = self.vlm.transcribe_image(
                    image_bytes=pix.tobytes("png"),
                    mime_type="image/png",
                    log_context={
                        **log_context,
                        "table_band": f"{band.first_row}-{band.last_row}",
                    },
                    user_prompt=VLM_TABLE_BAND_USER,
                )
                cleaned = normalize_table_band_transcription(result)
                if not is_valid_table_band_transcription(
                    cleaned, expected_rows=band.last_row - band.first_row + 1
                ):
                    return None, calls
                rows = [line.strip().strip("|").strip() for line in cleaned.splitlines()]
                counts = {len(row.split("|")) for row in rows}
                if len(counts) != 1:
                    return None, calls
                current = counts.pop()
                if column_count is not None and column_count != current:
                    return None, calls
                column_count = current
                outputs.extend(rows)
            return "\n".join(outputs), calls
        except Exception as exc:
            logger.info(
                "analysis table-banding fallback document_id=%s page_no=%s err=%s",
                log_context.get("document_id"),
                log_context.get("page_no"),
                type(exc).__name__,
            )
            return None, calls

    def _try_transcribe_kosa_work_table_crop(
        self,
        page: pymupdf.Page,
        *,
        log_context: dict,
        remaining_calls: int,
    ) -> tuple[str | None, int]:
        """One rule-free KOSA p1 work-table region call, memory-only.

        The normalized rectangle is a cautious fallback for empty VLM scans,
        not a general KOSA layout assumption: unverified crops are discarded.
        """
        if self.vlm is None or remaining_calls < 1 or page.number != 0:
            return None, 0
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            return None, 0
        calls = 0
        try:
            clip = pymupdf.Rect(
                rect.x0,
                rect.y0 + rect.height * 0.14,
                rect.x1,
                rect.y0 + rect.height * 0.51,
            )
            # The dedicated work-table crop was validated at 240 dpi on a
            # scanned KOSA PDF (no PDF text or detectable table rules).
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(240 / 72, 240 / 72),
                clip=clip,
                alpha=False,
            )
            prompt = (
                "이미지의 근무경력 표만 정확히 전사하세요. "
                "각 행은 '회사명 | 시작일 ~ 종료일 | 담당업무' 형식으로 "
                "한 줄씩 출력하세요. "
                "표에서 보이는 회사명과 날짜를 원문 그대로 옮기세요. "
                "날짜가 두 줄로 나뉘어 있으면 같은 셀의 값으로 읽으세요. "
                "추측하거나 날짜를 보정하지 마세요. "
                "이미지에 없는 행은 만들지 마세요. "
                "상단 신원정보나 다른 표는 출력하지 마세요."
            )
            calls = 1
            transcribed = self.vlm.transcribe_image(
                image_bytes=pix.tobytes("png"),
                mime_type="image/png",
                user_prompt=prompt,
                log_context=log_context,
            )
            return normalize_kosa_cropped_employment_rows(transcribed), calls
        except Exception as exc:
            logger.info(
                "KOSA work-table crop fallback failed document_id=%s page_no=%s err=%s",
                log_context.get("document_id"),
                log_context.get("page_no"),
                type(exc).__name__,
            )
            return None, calls

    def _try_transcribe_kosa_project_table_crop(
        self,
        page: pymupdf.Page,
        *,
        log_context: dict,
        remaining_calls: int,
    ) -> tuple[str | None, int]:
        """Separately transcribe lower p1 technical-history rows, memory-only.

        A failed or unbudgeted lower crop rejects the paired work-only
        replacement, preserving the original stored page rather than hiding
        projects. The screenshot region was measured on the KOSA scan.
        """
        if self.vlm is None or remaining_calls < 1 or page.number != 0:
            return None, 0
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            return None, 0
        calls = 0
        try:
            clip = pymupdf.Rect(
                rect.x0,
                rect.y0 + rect.height * 0.49,
                rect.x1,
                rect.y0 + rect.height * 0.995,
            )
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(240 / 72, 240 / 72),
                clip=clip,
                alpha=False,
            )
            prompt = (
                "이미지는 KOSA 경력증명서 1페이지 하단입니다. "
                "기술경력 표에 실제로 보이는 프로젝트 행을 전사하세요. "
                "각 행을 '사업명 | 참여기간 | 발주처 | 소속회사 | 직위 | 담당업무' "
                "형식으로 한 줄씩 출력하세요. "
                "줄바꿈된 셀은 같은 행에 연결하되, 서로 다른 행을 합치지 마세요. "
                "보이지 않거나 불확실한 값은 추측하지 말고 빈 값으로 두세요. "
                "근무경력 회사명과 입퇴사일을 프로젝트로 변환하지 마세요. "
                "개인 신원정보와 문서 발급정보는 출력하지 마세요."
            )
            calls = 1
            transcribed = self.vlm.transcribe_image(
                image_bytes=pix.tobytes("png"),
                mime_type="image/png",
                user_prompt=prompt,
                log_context=log_context,
            )
            return normalize_kosa_cropped_project_rows(transcribed), calls
        except Exception as exc:
            logger.info(
                "KOSA project-table crop fallback failed document_id=%s page_no=%s err=%s",
                log_context.get("document_id"),
                log_context.get("page_no"),
                type(exc).__name__,
            )
            return None, calls

    def _try_transcribe_kosa_empty_page_tiles(
        self,
        page: pymupdf.Page,
        *,
        log_context: dict,
        remaining_calls: int,
    ) -> tuple[str | None, int]:
        """Rule-free fallback for a scanned DOC-KOSA page with empty VLM text.

        Three overlapping vertical strips make dense career rows legible.
        Only completed, plausible transcription replaces the in-memory text.
        This never alters DocumentPage or persisted source files.
        """
        if self.vlm is None or remaining_calls < 3:
            return None, 0
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            return None, 0
        calls = 0
        parts: list[str] = []
        # Small overlaps avoid losing rows at crop boundaries. The prompt
        # explicitly forbids reconstructing rows that are only partly visible.
        ranges = ((0.00, 0.36), (0.32, 0.69), (0.65, 1.00))
        prompt = (
            "KOSA 경력증명서의 스캔 이미지 일부입니다. 보이는 글자만 전사하세요. "
            "근무경력 표의 회사명, 입사일, 퇴사일, 근무기간 등 각 셀을 "
            "원문 순서와 행 구분에 맞춰 기록하세요. "
            "표 선이 없거나 글자가 흐려도 보이지 않는 행, 회사명, 날짜를 "
            "추측하거나 생성하지 마세요. 잘린 행은 복원하지 마세요. "
            "근무경력과 기술경력은 서로 다른 표이므로 반드시 구분하세요. "
            "실제로 보이는 구역 제목(근무경력, 기술경력, 기술자격 등)을 "
            "생략하지 말고 그대로 기록하세요. 없는 제목은 만들지 마세요. "
            "기술경력의 참여사업명, 발주처, 소속사, 참여기간을 "
            "근무경력의 회사명과 입퇴사일로 바꾸지 마세요. "
            "프로젝트 참여기간을 근무경력으로 바꾸지 마세요. "
            "빈 칸을 반복 출력하거나 Markdown 빈 표를 생성하지 마세요."
        )
        try:
            dpi = min(240, max(180, int(self.settings.analysis_pdf_render_dpi)))
            zoom = dpi / 72.0
            for index, (top, bottom) in enumerate(ranges, 1):
                clip = pymupdf.Rect(
                    rect.x0,
                    rect.y0 + rect.height * top,
                    rect.x1,
                    rect.y0 + rect.height * bottom,
                )
                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(zoom, zoom),
                    clip=clip,
                    alpha=False,
                )
                calls += 1
                result = self.vlm.transcribe_image(
                    image_bytes=pix.tobytes("png"),
                    mime_type="image/png",
                    log_context={
                        **log_context,
                        "page_tile": f"{index}/3",
                    },
                    user_prompt=prompt,
                )
                cleaned = (result or "").strip()
                if not cleaned or len(cleaned) > 8000:
                    return None, calls
                parts.append(cleaned)
            # Keep original source order and every visible row. Duplicated
            # boundary rows are safer for review than silently lost rows.
            recovered = "\n".join(parts)
            if not is_plausible_kosa_employment_recovery(recovered):
                return None, calls
            return recovered, calls
        except Exception as exc:
            logger.info(
                "KOSA page-tile fallback failed document_id=%s page_no=%s err=%s",
                log_context.get("document_id"),
                log_context.get("page_no"),
                type(exc).__name__,
            )
            return None, calls

    def _is_image(self, doc: DocumentSnapshot) -> bool:
        ext = (doc.extension or "").lower().strip()
        return ext in IMAGE_EXTENSIONS

    def _open_render_source(self, doc: DocumentSnapshot) -> pymupdf.Document | None:
        """Prefer preview PDF, then original PDF; images handled separately."""
        ext = (doc.extension or "").lower().strip()
        keys: list[str] = []
        if doc.preview_storage_key:
            keys.append(doc.preview_storage_key)
        if ext == "pdf":
            keys.append(doc.storage_key)
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            try:
                data = self._download(key)
                return pymupdf.open(stream=data, filetype="pdf")
            except Exception:
                logger.info(
                    "analysis render open failed document_id=%s key=%s",
                    doc.id,
                    key,
                )
                continue
        return None

    def _download(self, key: str) -> bytes:
        obj = self.storage.get(key)
        try:
            data = b"".join(obj.iter_chunks())
        finally:
            obj.close()
        if not data:
            raise AIProviderError("empty object")
        return data

    def _render_page_png(self, doc: pymupdf.Document, page_index: int) -> bytes:
        dpi = int(self.settings.analysis_pdf_render_dpi)
        zoom = max(dpi, 72) / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return pix.tobytes("png")
