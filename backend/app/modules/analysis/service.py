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

from app.ai.prompts.profile_extract_v2 import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.ai.providers.errors import AIProviderError, AIResponseValidationError
from app.ai.providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from app.ai.providers.vlm import OpenAICompatibleVLMProvider, VLMProvider
from app.ai.schemas.profile_candidate import ProfileCandidateDocument
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
)
from app.storage.base import ObjectStorage
from app.storage.s3 import get_object_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RunContext:
    run_id: UUID
    person_id: UUID
    base_profile_version: int | None
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
        self, payload: CreateAnalysisRequest, actor_user_id: UUID
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

        run = self.repo.create_run(
            person_id=payload.person_id,
            base_profile_version=profile.profile_version,
            llm_model=self.settings.llm_model,
            vlm_model=self.settings.vlm_model,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
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

            bundle = self.source_builder.build(
                list(claimed.documents),
                log_context={"analysis_run_id": str(run_id)},
            )
            prompt_source = bundle.build_prompt_source(
                int(self.settings.analysis_max_total_text_chars)
            )
            blocks = prompt_source.text
            if not blocks.strip():
                raise AIProviderError("no usable text for profile analysis")

            raw = self.llm.complete_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(
                    code_catalog=claimed.code_catalog_text,
                    document_blocks=blocks,
                ),
                log_context={
                    "analysis_run_id": str(run_id),
                    "document_count": len(claimed.documents),
                    "vlm_pages": bundle.total_vlm_pages,
                },
            )
            if not isinstance(raw, dict):
                raise AIResponseValidationError("profile JSON root is not an object")

            catalog_map = {
                code: (code_type, active)
                for code, code_type, active in claimed.catalog
            }
            # source_ref validation uses only pages actually present in the LLM prompt.
            candidate = normalize_candidate(
                raw,
                catalog=catalog_map,
                allowed_documents=prompt_source.allowed_documents,
                settings=self.settings,
                page_texts=prompt_source.page_texts,
            )

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
        if isinstance(exc, AIResponseValidationError):
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
        if diff.review_status != "PENDING":
            raise AnalysisStateConflictError(
                "이미 결정된 Diff는 재결정할 수 없습니다."
            )

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
            raise ValidationAppError("review_status를 PENDING으로 설정할 수 없습니다.")
        if diff.review_status != "PENDING":
            raise AnalysisStateConflictError(
                "이미 결정된 Diff는 재결정할 수 없습니다."
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
            return result
        except IntegrityError as exc:
            self.db.rollback()
            raise ConfirmValidationError(
                "확정 중 데이터 제약 조건을 위반했습니다."
            ) from exc
        except Exception:
            self.db.rollback()
            raise

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
