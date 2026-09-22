"""Detailed AI profile analysis orchestration."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai.prompts.profile_extract import (
    UnknownProfilePromptVersionError,
    current_profile_prompt,
    resolve_profile_prompt,
)
from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from app.ai.providers.vlm import OpenAICompatibleVLMProvider, VLMProvider
from app.ai.schemas.profile_candidate import (
    PROFILE_SCALAR_FIELDS,
    ProfileCandidateDocument,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AIQueueUnavailableError,
    AnalysisStateConflictError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.modules.analysis.diff_engine import DiffSpec, build_diffs
from app.modules.analysis.normalize import normalize_candidate
from app.modules.analysis.repository import AnalysisRepository
from app.modules.analysis.schemas import (
    AnalysisDetail,
    AnalysisListItem,
    BulkDiffRequest,
    ConfirmAnalysisResponseData,
    CreateAnalysisRequest,
    CreateAnalysisResponseData,
    DiffCounts,
    DiffDecisionRequest,
    DiffItemResponse,
    DocumentBrief,
    EvidenceLite,
    PageMeta,
    PersonBrief,
)
from app.modules.analysis.source_builder import (
    AnalysisSourceBuilder,
    DocumentSnapshot,
    PageSnapshot,
    VLMPageTranscription,
)
from app.modules.search.document_chunk_sync import DocumentChunkSyncService
from app.storage.base import ObjectStorage
from app.storage.s3 import get_object_storage

logger = logging.getLogger(__name__)

_CODE_ENTITY_EXPECTED_TYPE: dict[str, str] = {
    "JOB": "JOB",
    "TECH": "TECH",
    "EXP": "EXP",
}
_PROJECT_CODE_FIELD_EXPECTED_TYPE: dict[str, str] = {
    "jobs": "JOB",
    "skills": "TECH",
    "expertise": "EXP",
    "business_domains": "BIZ",
    "customer_types": "CUSTOMER_TYPE",
}

# Candidate quality guard (empty / sparse LLM output).
# Sparse applies only to rich profile-like document types with enough source text,
# so CERT / short docs that legitimately yield 0–1 items are not retried as sparse.
_SPARSE_MIN_SOURCE_CHARS = 500
_SPARSE_MAX_QUALITY_SCORE = 1
_RICH_PROFILE_DOC_TYPES = frozenset({
    "DOC-PROFILE",
    "DOC-RESUME",
    "DOC-CAREER",
    "DOC-KOSA",
})


class InsufficientCandidateError(Exception):
    """LLM returned empty/sparse candidate after the allowed retry."""

    USER_MESSAGE = (
        "AI 분석 결과에서 유효한 프로필 정보를 충분히 추출하지 못했습니다. "
        "다시 분석해 주세요."
    )


def _profile_scalar_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def candidate_quality_score(candidate: ProfileCandidateDocument) -> int:
    """Count substantive profile items (scalars + list entities).

    ``summary`` / ``analysis`` metadata alone do not contribute.
    """
    score = 0
    for field in PROFILE_SCALAR_FIELDS:
        if _profile_scalar_filled(getattr(candidate.profile, field, None)):
            score += 1
    score += len(candidate.jobs)
    score += len(candidate.skills)
    score += len(candidate.expertise)
    score += len(candidate.employment_history)
    score += len(candidate.education)
    score += len(candidate.certifications)
    score += len(candidate.projects)
    return score


def candidate_is_empty(candidate: ProfileCandidateDocument) -> bool:
    return candidate_quality_score(candidate) == 0


def _documents_are_rich_profile_type(documents: tuple[DocumentSnapshot, ...] | list[DocumentSnapshot]) -> bool:
    return any(
        (doc.document_type_code or "").strip().upper() in _RICH_PROFILE_DOC_TYPES
        for doc in documents
    )


def candidate_is_sparse(
    candidate: ProfileCandidateDocument,
    *,
    documents: tuple[DocumentSnapshot, ...] | list[DocumentSnapshot],
    source_char_count: int,
) -> bool:
    """True when a rich PROFILE/RESUME/CAREER/KOSA source yields almost no items."""
    if source_char_count < _SPARSE_MIN_SOURCE_CHARS:
        return False
    if not _documents_are_rich_profile_type(documents):
        return False
    return candidate_quality_score(candidate) <= _SPARSE_MAX_QUALITY_SCORE


def candidate_needs_llm_retry(
    candidate: ProfileCandidateDocument,
    *,
    documents: tuple[DocumentSnapshot, ...] | list[DocumentSnapshot],
    source_char_count: int,
) -> bool:
    if candidate_is_empty(candidate):
        return True
    return candidate_is_sparse(
        candidate, documents=documents, source_char_count=source_char_count
    )


def _extract_candidate_code(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, dict):
        return None
    for key in (
        "code",
        "job_code",
        "tech_code",
        "exp_code",
        "biz_code",
        "customer_type_code",
    ):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _code_mapping_expected_type(diff: AnalysisDiffItem) -> str | None:
    expected = _CODE_ENTITY_EXPECTED_TYPE.get(diff.entity_type)
    if expected is not None:
        return expected
    if diff.entity_type == "PROJECT" and diff.field_name:
        return _PROJECT_CODE_FIELD_EXPECTED_TYPE.get(diff.field_name)
    return None



@dataclass(frozen=True)
class _RunContext:
    run_id: UUID
    person_id: UUID
    base_profile_version: int | None
    prompt_version: str | None
    schema_version: str | None
    documents: tuple[DocumentSnapshot, ...]
    catalog: tuple[tuple[str, str, bool], ...]  # code, type, active
    code_catalog_text: str


class AnalysisService:
    def __init__(
        self,
        db: Session,
        *,
        storage: ObjectStorage | None = None,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        vlm: VLMProvider | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.storage = storage or get_object_storage()
        self.repo = AnalysisRepository(db)
        self.llm = llm or OpenAICompatibleLLMProvider(self.settings)
        self.vlm = vlm or OpenAICompatibleVLMProvider(self.settings)
        self.source_builder = AnalysisSourceBuilder(
            self.storage, settings=self.settings, vlm=self.vlm
        )

    # ------------------------------------------------------------------ create

    def create_analysis(
        self, payload: CreateAnalysisRequest, actor_user_id: UUID | None
    ) -> CreateAnalysisResponseData:
        if payload.analysis_type != "PROFILE":
            raise ValidationAppError("analysis_type은 PROFILE만 지원합니다.")

        person = self.repo.get_person(payload.person_id, for_update=True)
        if person is None or person.deleted_at is not None or person.status == "DELETED":
            raise NotFoundError("인력을 찾을 수 없습니다.")

        profile = self.repo.get_profile(payload.person_id, for_update=True)
        if profile is None:
            raise NotFoundError("인력 프로필을 찾을 수 없습니다.")

        docs = self.repo.list_documents_by_ids(payload.document_ids)
        if len(docs) != len(payload.document_ids):
            raise NotFoundError("일부 문서를 찾을 수 없습니다.")

        for document, group, _name in docs:
            if group.person_id != payload.person_id:
                raise ValidationAppError(
                    "선택한 문서가 해당 인력에 속하지 않습니다."
                )
            if document.processing_status != "READY":
                raise ValidationAppError(
                    f"문서가 READY 상태가 아닙니다: {document.original_filename}"
                )

        prompt = current_profile_prompt()
        run = self.repo.create_run(
            person_id=payload.person_id,
            base_profile_version=profile.profile_version,
            llm_model=self.settings.llm_model,
            vlm_model=self.settings.vlm_model,
            prompt_version=prompt.prompt_version,
            schema_version=prompt.schema_version,
        )
        self.repo.add_run_documents(run.id, payload.document_ids)
        self.repo.add_audit(
            action_type="ANALYSIS_CREATE",
            actor_user_id=actor_user_id,
            target_id=run.id,
            after={
                "status": "QUEUED",
                "person_id": str(payload.person_id),
                "document_ids": [str(i) for i in payload.document_ids],
                "base_profile_version": profile.profile_version,
            },
        )
        self.db.commit()

        from app.tasks.analysis_tasks import enqueue_profile_analysis

        try:
            enqueue_profile_analysis(run.id, actor_user_id)
        except Exception:
            logger.exception("analysis enqueue failed run_id=%s", run.id)
            run = self.repo.get_run(run.id, for_update=True)
            if run is not None and run.status == "QUEUED":
                self.repo.mark_failed(run, "AI queue unavailable")
                self.repo.add_audit(
                    action_type="ANALYSIS_ENQUEUE_FAILED",
                    actor_user_id=actor_user_id,
                    target_id=run.id,
                    after={"status": "FAILED"},
                )
                self.db.commit()
            raise AIQueueUnavailableError(
                "분석 작업을 큐에 등록할 수 없습니다. 잠시 후 다시 시도해 주세요."
            )

        return CreateAnalysisResponseData(analysis_id=run.id, status="QUEUED")

    def create_analysis_for_ready_document(
        self, document_id: UUID
    ) -> CreateAnalysisResponseData | None:
        """Auto-start PROFILE analysis for a newly READY document.

        Idempotent: skips when any AnalysisRun already references the document.
        Returns None when skipped; raises only on unexpected create/enqueue errors
        (caller should not mark the Document FAILED).
        """
        docs = self.repo.list_documents_by_ids([document_id])
        if not docs:
            logger.info(
                "auto analysis skipped document_id=%s reason=document_unavailable",
                document_id,
            )
            return None

        document, group, _name = docs[0]
        if document.processing_status != "READY":
            logger.info(
                "auto analysis skipped document_id=%s reason=not_ready status=%s",
                document_id,
                document.processing_status,
            )
            return None

        person = self.repo.get_person(group.person_id, for_update=True)
        if person is None or person.deleted_at is not None or person.status == "DELETED":
            logger.info(
                "auto analysis skipped document_id=%s reason=person_unavailable",
                document_id,
            )
            return None

        if self.repo.has_any_run_for_document(document_id):
            logger.info(
                "auto analysis skipped document_id=%s reason=analysis_run_exists",
                document_id,
            )
            return None

        payload = CreateAnalysisRequest(
            person_id=group.person_id,
            document_ids=[document_id],
            analysis_type="PROFILE",
        )
        result = self.create_analysis(payload, document.uploaded_by)
        logger.info(
            "auto analysis started document_id=%s analysis_run_id=%s",
            document_id,
            result.analysis_id,
        )
        return result

    # -------------------------------------------------------------------- run

    def run_analysis(
        self,
        run_id: UUID,
        *,
        llm: LLMProvider | None = None,
        vlm: VLMProvider | None = None,
        actor_user_id: UUID | None = None,
    ) -> str:
        """Execute a QUEUED analysis run. Returns final status string."""
        if llm is not None:
            self.llm = llm
        if vlm is not None:
            self.vlm = vlm
            self.source_builder = AnalysisSourceBuilder(
                self.storage, settings=self.settings, vlm=self.vlm
            )

        claimed = self._claim_and_snapshot(run_id)
        if isinstance(claimed, str):
            return claimed

        try:
            if not claimed.documents:
                raise AIProviderError("no documents for analysis")

            try:
                prompt = resolve_profile_prompt(claimed.prompt_version)
            except UnknownProfilePromptVersionError as exc:
                raise AIProviderError(str(exc)) from exc

            bundle = self.source_builder.build(
                list(claimed.documents),
                log_context={"analysis_run_id": str(run_id)},
            )
            # Persist successful VLM page text before LLM so retries/search can
            # reuse it even when the analysis later fails.
            if bundle.vlm_transcriptions:
                self._persist_vlm_transcriptions(
                    bundle.vlm_transcriptions,
                    analysis_run_id=run_id,
                )
            prompt_source = bundle.build_prompt_source(
                int(self.settings.analysis_max_total_text_chars)
            )
            blocks = prompt_source.text
            if not blocks.strip():
                raise AIProviderError("no usable text for profile analysis")

            user_prompt = prompt.build_user_prompt(
                code_catalog=claimed.code_catalog_text,
                document_blocks=blocks,
            )
            catalog_map = {
                code: (code_type, active)
                for code, code_type, active in claimed.catalog
            }
            source_char_count = len(blocks.strip())
            base_log = {
                "analysis_run_id": str(run_id),
                "document_count": len(claimed.documents),
                "vlm_pages": bundle.total_vlm_pages,
                "prompt_version": prompt.prompt_version,
                "source_char_count": source_char_count,
            }

            def _llm_normalize(*, attempt: int) -> ProfileCandidateDocument:
                raw = self.llm.complete_json(
                    system_prompt=prompt.system_prompt,
                    user_prompt=user_prompt,
                    log_context={**base_log, "attempt": attempt},
                )
                if not isinstance(raw, dict):
                    raise AIResponseValidationError(
                        "profile JSON root is not an object"
                    )
                # source_ref validation uses only pages actually present in the LLM prompt.
                return normalize_candidate(
                    raw,
                    catalog=catalog_map,
                    allowed_documents=prompt_source.allowed_documents,
                    settings=self.settings,
                    page_texts=prompt_source.page_texts,
                )

            def _log_quality(*, attempt: int, candidate: ProfileCandidateDocument) -> None:
                empty = candidate_is_empty(candidate)
                sparse = candidate_is_sparse(
                    candidate,
                    documents=claimed.documents,
                    source_char_count=source_char_count,
                )
                logger.info(
                    "analysis candidate quality run_id=%s attempt=%s "
                    "document_count=%s vlm_pages=%s empty=%s sparse=%s "
                    "quality_score=%s",
                    run_id,
                    attempt,
                    len(claimed.documents),
                    bundle.total_vlm_pages,
                    empty,
                    sparse,
                    candidate_quality_score(candidate),
                )

            candidate = _llm_normalize(attempt=1)
            _log_quality(attempt=1, candidate=candidate)
            if candidate_needs_llm_retry(
                candidate,
                documents=claimed.documents,
                source_char_count=source_char_count,
            ):
                # Reuse the same prompt source; do not rebuild VLM/source.
                candidate = _llm_normalize(attempt=2)
                _log_quality(attempt=2, candidate=candidate)
                if candidate_needs_llm_retry(
                    candidate,
                    documents=claimed.documents,
                    source_char_count=source_char_count,
                ):
                    raise InsufficientCandidateError()

            if claimed.base_profile_version is None:
                raise AIProviderError("missing base_profile_version")

            base_snap = self.repo.get_profile_revision_snapshot(
                claimed.person_id, claimed.base_profile_version
            )
            if base_snap is None:
                run = self.repo.get_run(run_id, for_update=True)
                if run is not None:
                    self.repo.mark_failed(
                        run,
                        f"base profile revision {claimed.base_profile_version} not found",
                    )
                    self.repo.add_audit(
                        action_type="ANALYSIS_FAILED",
                        actor_user_id=actor_user_id,
                        target_id=run_id,
                        after={"status": "FAILED", "reason": "missing_revision"},
                    )
                    self.db.commit()
                return "FAILED"

            specs = build_diffs(candidate, base_snap)
            return self._persist_reviewing(
                run_id,
                candidate=candidate,
                specs=specs,
                actor_user_id=actor_user_id,
            )
        except Exception as exc:
            logger.exception("analysis failed run_id=%s", run_id)
            self.db.rollback()
            return self._fail_run(run_id, exc, actor_user_id=actor_user_id)

    def _persist_vlm_transcriptions(
        self,
        transcriptions: list[VLMPageTranscription],
        *,
        analysis_run_id: UUID,
    ) -> None:
        """Write successful VLM page text and re-ensure DocumentChunk sync jobs.

        Failures propagate to ``run_analysis`` so analysis does not reach
        REVIEWING without persisting acquired VLM text. Already-committed page
        text is not rolled back if LLM fails later.
        """
        if not transcriptions:
            return

        persisted = 0
        skipped = 0
        group_ids: set[UUID] = set()
        for item in transcriptions:
            updated = self.repo.try_update_page_vlm_transcription(
                document_id=item.document_id,
                page_no=item.page_no,
                expected_extracted_text=item.expected_extracted_text,
                expected_extraction_method=item.expected_extraction_method,
                expected_layout_json=item.expected_layout_json,
                persisted_text=item.persisted_text,
                extraction_method=item.extraction_method,
                layout_json=item.layout_json,
            )
            if not updated:
                skipped += 1
                logger.info(
                    "vlm page persist skipped analysis_run_id=%s document_id=%s "
                    "page_no=%s",
                    analysis_run_id,
                    item.document_id,
                    item.page_no,
                )
                continue
            persisted += 1
            group_id = self.repo.get_document_group_id(item.document_id)
            if group_id is not None:
                group_ids.add(group_id)

        sync = DocumentChunkSyncService(self.db)
        for group_id in sorted(group_ids, key=str):
            sync.ensure_sync_job(group_id)

        self.db.commit()
        logger.info(
            "vlm page persist analysis_run_id=%s persisted=%s skipped=%s "
            "groups=%s vlm_pages=%s",
            analysis_run_id,
            persisted,
            skipped,
            len(group_ids),
            len(transcriptions),
        )

    def _claim_and_snapshot(self, run_id: UUID) -> _RunContext | str:
        claimed = self.repo.try_claim_analysis(run_id)
        if claimed is None:
            existing = self.repo.get_run(run_id)
            if existing is None:
                self.db.rollback()
                return "NOT_FOUND"
            status = existing.status
            self.db.rollback()
            return status

        doc_ids = self.repo.list_run_document_ids(run_id)
        doc_rows = self.repo.list_documents_by_ids(doc_ids)
        documents: list[DocumentSnapshot] = []
        for document, group, type_name in doc_rows:
            pages = self.repo.list_document_pages(document.id)
            documents.append(
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
                            page_no=p.page_no,
                            extracted_text=p.extracted_text,
                            layout_json=p.layout_json,
                            extraction_method=p.extraction_method,
                        )
                        for p in pages
                    ],
                )
            )

        codes = self.repo.list_active_codes()
        catalog = tuple((c.code, c.code_type, bool(c.is_active)) for c in codes)
        aliases = self.repo.list_aliases_for_codes([c.code for c in codes])
        catalog_text = self._format_code_catalog(codes, aliases)

        # Release row lock before Storage / VLM / LLM work.
        self.db.commit()
        return _RunContext(
            run_id=run_id,
            person_id=claimed.person_id,
            base_profile_version=claimed.base_profile_version,
            prompt_version=claimed.prompt_version,
            schema_version=claimed.schema_version,
            documents=tuple(documents),
            catalog=catalog,
            code_catalog_text=catalog_text,
        )

    def _format_code_catalog(
        self, codes: list, aliases: dict[str, list[str]] | None = None
    ) -> str:
        max_chars = int(self.settings.analysis_code_context_max_chars)
        alias_map = aliases or {}
        lines: list[str] = []
        used = 0
        current_type: str | None = None
        for row in codes:
            if row.code_type != current_type:
                current_type = row.code_type
                header = f"\n[{current_type}]\n"
                if used + len(header) > max_chars:
                    break
                lines.append(header)
                used += len(header)
            alias_part = "|".join(alias_map.get(row.code, []))
            if alias_part:
                line = f"{row.code}\t{row.name}\t{alias_part}\n"
            else:
                line = f"{row.code}\t{row.name}\n"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        return "".join(lines).strip()

    def _persist_reviewing(
        self,
        run_id: UUID,
        *,
        candidate: ProfileCandidateDocument,
        specs: list[DiffSpec],
        actor_user_id: UUID | None,
    ) -> str:
        run = self.repo.get_run(run_id, for_update=True)
        if run is None:
            self.db.rollback()
            return "NOT_FOUND"
        if run.status != "PROCESSING":
            status = run.status
            self.db.rollback()
            return status

        confidence = candidate.analysis.overall_confidence
        self.repo.replace_diffs(run_id, [s.to_persist_dict() for s in specs])
        self.repo.mark_reviewing(
            run,
            candidate_json=candidate.to_storage_dict(),
            overall_confidence=confidence,
        )
        self.repo.add_audit(
            action_type="ANALYSIS_REVIEWING",
            actor_user_id=actor_user_id,
            target_id=run_id,
            after={
                "status": "REVIEWING",
                "diff_count": len(specs),
                "overall_confidence": float(confidence) if confidence is not None else None,
            },
        )
        self.db.commit()
        return "REVIEWING"

    def _fail_run(
        self, run_id: UUID, exc: Exception, *, actor_user_id: UUID | None
    ) -> str:
        run = self.repo.get_run(run_id, for_update=True)
        if run is None:
            self.db.rollback()
            return "NOT_FOUND"
        if run.status in {"CONFIRMED", "CANCELLED", "REVIEWING"}:
            status = run.status
            self.db.rollback()
            return status
        if isinstance(exc, InsufficientCandidateError):
            message = InsufficientCandidateError.USER_MESSAGE
        elif isinstance(exc, AIResponseValidationError):
            message = "AI response validation failed"
        elif isinstance(exc, AIProviderError):
            message = "AI provider error"
        else:
            message = "analysis failed"
        self.repo.mark_failed(run, message)
        self.repo.add_audit(
            action_type="ANALYSIS_FAILED",
            actor_user_id=actor_user_id,
            target_id=run_id,
            after={"status": "FAILED", "error": message},
        )
        self.db.commit()
        return "FAILED"

    # ------------------------------------------------------------------- list

    def list_analyses(
        self,
        *,
        status: str | None = None,
        person_id: UUID | None = None,
        sort: str = "created_desc",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AnalysisListItem], PageMeta]:
        rows, total = self.repo.list_runs(
            status=status,
            person_id=person_id,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        items: list[AnalysisListItem] = []
        for run in rows:
            items.append(self._to_list_item(run))
        total_pages = math.ceil(total / page_size) if page_size else 0
        return items, PageMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    def get_analysis(self, analysis_id: UUID) -> AnalysisDetail:
        run = self.repo.get_run(analysis_id)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        return self._to_detail(run)

    def list_diffs(
        self,
        analysis_id: UUID,
        *,
        change_types: str | None = None,
        review_status: str | None = None,
        entity_type: str | None = None,
    ) -> list[DiffItemResponse]:
        run = self.repo.get_run(analysis_id)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        parsed_types = None
        if change_types:
            parsed_types = [p.strip().upper() for p in change_types.split(",") if p.strip()]
        rows = self.repo.list_diffs(
            analysis_id,
            change_types=parsed_types,
            review_status=review_status,
            entity_type=entity_type,
        )
        evidence_map: dict[UUID, list[EvidenceLite]] | None = None
        if run.status == "CONFIRMED" and rows:
            from app.modules.evidence.repository import EvidenceRepository

            by_diff = EvidenceRepository(self.db).list_evidence_by_diff_ids(
                [row.id for row in rows]
            )
            evidence_map = {
                diff_id: [
                    EvidenceLite(
                        id=ev.id,
                        document_id=ev.document_id,
                        page_no=ev.page_no,
                        quote_text=ev.quote_text,
                    )
                    for ev in evidences
                ]
                for diff_id, evidences in by_diff.items()
            }
        return [
            self._to_diff_response(row, evidence_map=evidence_map) for row in rows
        ]

    # ----------------------------------------------------------------- review

    def review_diff(
        self,
        analysis_id: UUID,
        diff_id: UUID,
        payload: DiffDecisionRequest,
        actor_user_id: UUID,
    ) -> DiffItemResponse:
        run = self.repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        if run.status != "REVIEWING":
            raise AnalysisStateConflictError(
                f"상태가 {run.status}인 분석은 검토할 수 없습니다."
            )

        diff = self.repo.get_diff(diff_id, for_update=True)
        if diff is None or diff.analysis_run_id != analysis_id:
            raise NotFoundError("Diff를 찾을 수 없습니다.")

        self._apply_decision(run, diff, payload, actor_user_id)
        self.repo.add_audit(
            action_type="ANALYSIS_DIFF_REVIEW",
            actor_user_id=actor_user_id,
            target_type="ANALYSIS_DIFF_ITEM",
            target_id=diff.id,
            after={"review_status": diff.review_status},
            metadata={
                "analysis_run_id": str(analysis_id),
                "person_id": str(run.person_id),
            },
        )
        self.db.commit()
        return self._to_diff_response(diff)

    def bulk_review(
        self,
        analysis_id: UUID,
        payload: BulkDiffRequest,
        actor_user_id: UUID,
    ) -> list[DiffItemResponse]:
        run = self.repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        if run.status != "REVIEWING":
            raise AnalysisStateConflictError(
                f"상태가 {run.status}인 분석은 검토할 수 없습니다."
            )

        if payload.review_status not in {"ACCEPTED", "REJECTED"}:
            raise ValidationAppError(
                "일괄 검토는 ACCEPTED 또는 REJECTED만 지원합니다."
            )

        # Validate all selected diffs first — no partial apply.
        locked: list[AnalysisDiffItem] = []
        for diff_id in payload.diff_ids:
            diff = self.repo.get_diff(diff_id, for_update=True)
            if diff is None or diff.analysis_run_id != analysis_id:
                raise NotFoundError(f"Diff를 찾을 수 없습니다: {diff_id}")
            if diff.review_status != "PENDING":
                raise AnalysisStateConflictError(
                    "이미 결정된 Diff가 포함되어 일괄 검토할 수 없습니다."
                )
            if payload.review_status == "ACCEPTED" and diff.change_type in {
                "CONFLICT",
                "REVIEW",
            }:
                raise ValidationAppError(
                    "CONFLICT/REVIEW Diff는 일괄 ACCEPTED할 수 없습니다."
                )
            locked.append(diff)

        results: list[DiffItemResponse] = []
        for diff in locked:
            decision = DiffDecisionRequest(review_status=payload.review_status)
            self._apply_decision(run, diff, decision, actor_user_id)
            results.append(self._to_diff_response(diff))

        self.repo.add_audit(
            action_type="ANALYSIS_DIFF_BULK_REVIEW",
            actor_user_id=actor_user_id,
            target_type="ANALYSIS_RUN",
            target_id=analysis_id,
            after={
                "review_status": payload.review_status,
                "diff_ids": [str(i) for i in payload.diff_ids],
            },
            metadata={
                "analysis_run_id": str(analysis_id),
                "person_id": str(run.person_id),
            },
        )
        self.db.commit()
        return results

    def _apply_decision(
        self,
        run: AnalysisRun,
        diff: AnalysisDiffItem,
        payload: DiffDecisionRequest,
        actor_user_id: UUID,
    ) -> None:
        from datetime import UTC, datetime

        status = payload.review_status
        if status == "PENDING":
            if diff.review_status == "PENDING":
                return
            if diff.review_status == "MERGED":
                raise ValidationAppError("병합 결정은 취소할 수 없습니다.")
            diff.review_status = "PENDING"
            diff.decided_value = None
            diff.decided_by = None
            diff.decided_at = None
            self.db.add(diff)
            self.db.flush()
            return

        if diff.review_status != "PENDING":
            raise AnalysisStateConflictError(
                "이미 결정된 Diff는 먼저 결정 취소 후 다시 검토해 주세요."
            )

        expected_code_type = _code_mapping_expected_type(diff)
        decision_value = payload.decided_value if status == "MODIFIED" else diff.new_value
        if status in {"ACCEPTED", "MODIFIED"} and expected_code_type is not None:
            code = _extract_candidate_code(decision_value)
            if not code:
                raise ValidationAppError(
                    f"{expected_code_type} 코드가 매핑되지 않은 항목은 승인할 수 없습니다. "
                    "코드를 지정하거나 반려해 주세요."
                )
            active_codes = {
                row.code
                for row in self.repo.list_active_codes([expected_code_type])
            }
            if code not in active_codes:
                raise ValidationAppError(
                    f"유효한 {expected_code_type} 코드를 지정해 주세요: {code}"
                )

        if status == "MODIFIED":
            if payload.decided_value is None:
                raise ValidationAppError("MODIFIED에는 decided_value가 필요합니다.")
            diff.decided_value = payload.decided_value
        elif status == "MERGED":
            path = diff.candidate_path or ""
            if (
                diff.entity_type != "PROJECT"
                or diff.change_type != "REVIEW"
                or diff.field_name is not None
                or not re.fullmatch(r"projects\[\d+\]", path)
            ):
                raise ValidationAppError(
                    "MERGED는 PROJECT root REVIEW Diff에만 사용할 수 있습니다."
                )
            target_id = payload.existing_target_id or diff.existing_target_id
            if target_id is None:
                raise ValidationAppError("MERGED에는 existing_target_id가 필요합니다.")
            project = self.repo.get_project(target_id, person_id=run.person_id)
            if project is None:
                raise ValidationAppError(
                    "existing_target_id가 해당 인력의 프로젝트가 아닙니다."
                )
            diff.existing_target_id = target_id
            if payload.decided_value is not None:
                diff.decided_value = payload.decided_value
        elif status in {"ACCEPTED", "REJECTED"}:
            if payload.decided_value is not None:
                diff.decided_value = payload.decided_value
        else:
            raise ValidationAppError(f"허용되지 않은 review_status입니다: {status}")

        diff.review_status = status
        diff.decided_by = actor_user_id
        diff.decided_at = datetime.now(UTC)
        self.db.add(diff)
        self.db.flush()

    # ------------------------------------------------------------------ retry

    def retry_analysis(
        self, analysis_id: UUID, actor_user_id: UUID
    ) -> CreateAnalysisResponseData:
        run = self.repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")
        if run.status != "FAILED":
            raise AnalysisStateConflictError(
                "FAILED 상태의 분석만 재시도할 수 있습니다."
            )

        self.repo.clear_candidate_and_diffs(run)
        self.repo.mark_queued(run)
        self.repo.add_audit(
            action_type="ANALYSIS_RETRY",
            actor_user_id=actor_user_id,
            target_id=run.id,
            after={"status": "QUEUED"},
        )
        self.db.commit()

        from app.tasks.analysis_tasks import enqueue_profile_analysis

        try:
            enqueue_profile_analysis(run.id, actor_user_id)
        except Exception:
            logger.exception("analysis retry enqueue failed run_id=%s", run.id)
            run = self.repo.get_run(run.id, for_update=True)
            if run is not None and run.status == "QUEUED":
                self.repo.mark_failed(run, "AI queue unavailable")
                self.db.commit()
            raise AIQueueUnavailableError(
                "분석 작업을 큐에 등록할 수 없습니다. 잠시 후 다시 시도해 주세요."
            )

        return CreateAnalysisResponseData(analysis_id=run.id, status="QUEUED")

    def cancel_analysis(
        self, analysis_id: UUID, actor_user_id: UUID
    ) -> CreateAnalysisResponseData:
        run = self.repo.get_run(analysis_id, for_update=True)
        if run is None:
            raise NotFoundError("분석을 찾을 수 없습니다.")

        if run.status == "CANCELLED":
            return CreateAnalysisResponseData(
                analysis_id=run.id, status="CANCELLED"
            )
        if run.status in {"QUEUED", "PROCESSING"}:
            raise AnalysisStateConflictError(
                "QUEUED/PROCESSING 상태의 분석은 현재 폐기할 수 없습니다."
            )
        if run.status == "CONFIRMED":
            raise AnalysisStateConflictError("확정된 분석은 폐기할 수 없습니다.")
        if run.status not in {"REVIEWING", "FAILED"}:
            raise AnalysisStateConflictError(
                f"상태가 {run.status}인 분석은 폐기할 수 없습니다."
            )

        before = {"status": run.status}
        self.repo.mark_cancelled(run, "사용자에 의해 폐기된 분석입니다.")
        self.repo.add_audit(
            action_type="ANALYSIS_CANCEL",
            actor_user_id=actor_user_id,
            target_id=run.id,
            before=before,
            after={"status": "CANCELLED"},
            metadata={
                "reason": "MANUAL",
                "person_id": str(run.person_id),
            },
        )
        self.db.commit()
        return CreateAnalysisResponseData(analysis_id=run.id, status="CANCELLED")

    def confirm_analysis(
        self,
        analysis_id: UUID,
        *,
        expected_profile_version: int,
        actor_user_id: UUID,
    ) -> ConfirmAnalysisResponseData:
        from sqlalchemy.exc import IntegrityError

        from app.core.exceptions import ConfirmValidationError
        from app.modules.analysis.confirm import confirm_analysis_run

        try:
            result = confirm_analysis_run(
                self.db,
                analysis_id=analysis_id,
                expected_profile_version=expected_profile_version,
                actor_user_id=actor_user_id,
            )
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConfirmValidationError(
                "확정 중 데이터 제약 조건을 위반했습니다."
            ) from exc
        except Exception:
            self.db.rollback()
            raise

        # Separate transaction after confirm commit — never rolls back Confirm.
        try:
            self._cancel_stale_reviewing_runs_after_confirm(
                person_id=result.person_id,
                current_profile_version=result.profile_version,
                confirmed_run_id=result.analysis_id,
                actor_user_id=actor_user_id,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "stale analysis cancel after confirm failed "
                "confirmed_run_id=%s person_id=%s",
                result.analysis_id,
                result.person_id,
            )

        return result

    def _cancel_stale_reviewing_runs_after_confirm(
        self,
        *,
        person_id: UUID,
        current_profile_version: int,
        confirmed_run_id: UUID,
        actor_user_id: UUID,
    ) -> None:
        stale_runs = self.repo.list_stale_reviewing_runs_for_update(
            person_id=person_id,
            current_profile_version=current_profile_version,
            exclude_run_id=confirmed_run_id,
        )
        for run in stale_runs:
            before = {"status": run.status}
            base_version = run.base_profile_version
            self.repo.mark_cancelled(
                run,
                "프로필이 변경되어 최신 상태와 맞지 않아 자동 폐기되었습니다.",
            )
            self.repo.add_audit(
                action_type="ANALYSIS_CANCEL",
                actor_user_id=actor_user_id,
                target_id=run.id,
                before=before,
                after={"status": "CANCELLED"},
                metadata={
                    "reason": "STALE_PROFILE_VERSION",
                    "person_id": str(person_id),
                    "base_profile_version": base_version,
                    "current_profile_version": current_profile_version,
                    "source_analysis_run_id": str(confirmed_run_id),
                },
            )

    # ----------------------------------------------------------------- mappers

    def _to_list_item(self, run: AnalysisRun) -> AnalysisListItem:
        profile = self.repo.get_profile(run.person_id)
        name = profile.name if profile is not None else ""
        doc_ids = self.repo.list_run_document_ids(run.id)
        docs = self.repo.list_documents_by_ids(doc_ids)
        labels = [
            f"{d.original_filename}"
            + (f" v{d.version_no}" if d.version_no else "")
            for d, _g, _n in docs
        ]
        counts = DiffCounts(**self.repo.count_diffs(run.id))
        return AnalysisListItem(
            analysis_id=run.id,
            person=PersonBrief(id=run.person_id, name=name),
            documents=labels,
            status=run.status,  # type: ignore[arg-type]
            counts=counts,
            base_profile_version=run.base_profile_version,
            llm_model=run.llm_model,
            prompt_version=run.prompt_version,
            overall_confidence=run.overall_confidence,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
        )

    def _to_detail(self, run: AnalysisRun) -> AnalysisDetail:
        profile = self.repo.get_profile(run.person_id)
        name = profile.name if profile is not None else ""
        doc_ids = self.repo.list_run_document_ids(run.id)
        docs = self.repo.list_documents_by_ids(doc_ids)
        documents = [
            DocumentBrief(
                id=d.id,
                original_filename=d.original_filename,
                version_no=d.version_no,
                document_type_code=g.document_type_code,
                document_type_name=n,
            )
            for d, g, n in docs
        ]
        counts = DiffCounts(**self.repo.count_diffs(run.id))
        candidate = run.candidate_json if isinstance(run.candidate_json, dict) else {}
        return AnalysisDetail(
            analysis_id=run.id,
            status=run.status,  # type: ignore[arg-type]
            person=PersonBrief(id=run.person_id, name=name),
            documents=documents,
            counts=counts,
            candidate_json=candidate,
            base_profile_version=run.base_profile_version,
            llm_model=run.llm_model,
            vlm_model=run.vlm_model,
            prompt_version=run.prompt_version,
            schema_version=run.schema_version,
            overall_confidence=run.overall_confidence,
            error_message=run.error_message,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _to_diff_response(
        self,
        row: AnalysisDiffItem,
        *,
        evidence_map: dict[UUID, list[EvidenceLite]] | None = None,
    ) -> DiffItemResponse:
        if evidence_map is not None:
            # CONFIRMED: only materialized AnalysisDiffEvidence — no raw fallback.
            evidence = evidence_map.get(row.id, [])
        else:
            evidence = _evidence_from_new_value(row.new_value)
        return DiffItemResponse(
            id=row.id,
            entity_type=row.entity_type,
            candidate_path=row.candidate_path,
            existing_target_id=row.existing_target_id,
            field_name=row.field_name,
            change_type=row.change_type,  # type: ignore[arg-type]
            old_value=row.old_value,
            new_value=_unwrap_new_value(row.new_value),
            confidence=row.confidence,
            evidence_type=row.evidence_type,
            review_status=row.review_status,  # type: ignore[arg-type]
            decided_value=row.decided_value,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            evidence=evidence,
        )


def _unwrap_new_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "source_refs" in value:
        # Scalar wrapped solely to carry source_refs.
        keys = set(value.keys()) - {"value", "source_refs"}
        if not keys:
            return value.get("value")
    return value


def _evidence_from_new_value(value: Any) -> list[EvidenceLite]:
    refs: list[Any] = []
    if isinstance(value, dict):
        refs = value.get("source_refs") or []
    out: list[EvidenceLite] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        doc_id = ref.get("document_id")
        parsed_doc: UUID | str | None = doc_id
        if doc_id:
            try:
                parsed_doc = UUID(str(doc_id))
            except (ValueError, TypeError):
                parsed_doc = str(doc_id)
        out.append(
            EvidenceLite(
                document_id=parsed_doc,
                page_no=ref.get("page_no"),
                quote_text=ref.get("quote_text"),
            )
        )
    return out


# Re-export helpers used by tasks / tests
__all__ = ["AnalysisService", "DiffCounts", "Decimal"]
