"""Confirm-time Evidence materialization (no commit)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisDiffItem, AnalysisRun
from app.db.models.document import DocumentPage
from app.db.models.evidence import Evidence, EvidenceLink
from app.db.models.person import (
    Certification,
    Education,
    EmploymentHistory,
    PersonExpertise,
    PersonJob,
    PersonSkill,
)
from app.db.models.project import Project
from app.modules.evidence.repository import EvidenceRepository
from app.modules.evidence.schemas import EVIDENCE_TARGET_TYPES

logger = logging.getLogger(__name__)

RELATION_TYPE_SUPPORTS = "SUPPORTS"

_PROJECT_ROOT_RE = re.compile(r"^projects\[\d+\]$")
_PROJECT_RELATION_FIELDS = frozenset(
    {"jobs", "skills", "expertise", "business_domains", "customer_types"}
)
_PROJECT_RELATION_FIELD_ORDER: tuple[str, ...] = (
    "jobs",
    "skills",
    "expertise",
    "business_domains",
    "customer_types",
)


@dataclass(frozen=True)
class AppliedEvidenceTarget:
    diff_id: UUID
    target_type: str
    target_id: UUID
    field_name: str | None = None


@dataclass
class AppliedTargetRegistry:
    """diff.id → confirmed operational target written by Confirm apply."""

    _items: dict[UUID, AppliedEvidenceTarget] = field(default_factory=dict)

    def register(
        self,
        diff_id: UUID,
        target_type: str,
        target_id: UUID,
        field_name: str | None = None,
    ) -> None:
        if target_type not in EVIDENCE_TARGET_TYPES:
            raise ValueError(f"invalid evidence target_type: {target_type}")
        self._items[diff_id] = AppliedEvidenceTarget(
            diff_id=diff_id,
            target_type=target_type,
            target_id=target_id,
            field_name=field_name,
        )

    def get(self, diff_id: UUID) -> AppliedEvidenceTarget | None:
        return self._items.get(diff_id)

    def items(self) -> list[AppliedEvidenceTarget]:
        return list(self._items.values())


@dataclass(frozen=True)
class EvidenceMaterializeResult:
    evidence_count: int = 0
    evidence_link_count: int = 0
    diff_evidence_count: int = 0


def extract_source_refs(new_value: Any) -> list[dict[str, Any]]:
    """Read AI Candidate source_refs from Diff.new_value only (never decided_value)."""
    if not isinstance(new_value, dict):
        return []
    refs = new_value.get("source_refs")
    if not isinstance(refs, list):
        return []
    out: list[dict[str, Any]] = []
    for ref in refs:
        if isinstance(ref, dict):
            out.append(ref)
    return out


def should_create_evidence_link(diff: AnalysisDiffItem) -> bool:
    if diff.change_type == "SAME":
        return True
    if diff.review_status in {"ACCEPTED", "MERGED"}:
        return True
    return False


def project_relation_field_name(relation_field: str, code: str) -> str:
    """Centralized PROJECT relation EvidenceLink.field_name convention."""
    return f"{relation_field}:{code}"


@dataclass(frozen=True)
class _RefWorkItem:
    refs: list[dict[str, Any]]
    """source_refs to materialize."""
    link_field_override: str | None = None
    """When set, EvidenceLink.field_name uses this instead of registry field."""
    require_project_relation: tuple[str, str] | None = None
    """(relation_field, code) must exist on Confirmed Project before SUPPORTS link."""


def _is_project_root_diff(diff: AnalysisDiffItem) -> bool:
    return (
        diff.entity_type == "PROJECT"
        and diff.field_name is None
        and bool(_PROJECT_ROOT_RE.fullmatch(diff.candidate_path or ""))
    )


def _relation_item_code(item: dict[str, Any]) -> str | None:
    for key in (
        "code",
        "job_code",
        "tech_code",
        "exp_code",
        "biz_code",
        "customer_type_code",
    ):
        raw = item.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None


def _iter_nested_project_relation_work(
    new_value: dict[str, Any],
) -> list[_RefWorkItem]:
    items: list[_RefWorkItem] = []
    for rel_field in _PROJECT_RELATION_FIELD_ORDER:
        rows = new_value.get(rel_field) or []
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            code = _relation_item_code(raw)
            if not code:
                continue
            refs = extract_source_refs(raw)
            if not refs:
                continue
            items.append(
                _RefWorkItem(
                    refs=refs,
                    link_field_override=project_relation_field_name(rel_field, code),
                    require_project_relation=(rel_field, code),
                )
            )
    return items


def materialize_confirm_evidence(
    db: Session,
    *,
    run: AnalysisRun,
    diffs: list[AnalysisDiffItem],
    person_id: UUID,
    registry: AppliedTargetRegistry,
) -> EvidenceMaterializeResult:
    """Create Evidence / AnalysisDiffEvidence / EvidenceLink. Does not commit."""
    repo = EvidenceRepository(db)
    allowed_docs = repo.list_run_document_ids(run.id)
    page_cache: dict[tuple[UUID, int], DocumentPage | None] = {}
    local_evidence: dict[tuple[Any, ...], Evidence] = {}
    diff_pairs: set[tuple[UUID, UUID]] = set()
    link_keys: set[tuple[UUID, str, UUID, str | None, str]] = set()

    evidence_count = 0
    link_count = 0
    diff_evidence_count = 0

    for diff in diffs:
        work_items: list[_RefWorkItem] = []
        root_refs = extract_source_refs(diff.new_value)
        if root_refs:
            work_items.append(_RefWorkItem(refs=root_refs))
        if _is_project_root_diff(diff) and isinstance(diff.new_value, dict):
            work_items.extend(_iter_nested_project_relation_work(diff.new_value))
        if not work_items:
            continue

        allow_links = should_create_evidence_link(diff)
        base_target = None
        if allow_links:
            base_target = registry.get(diff.id) or _resolve_existing_target(
                db, diff=diff, person_id=person_id
            )

        for work in work_items:
            for ref in work.refs:
                validated = _validate_source_ref(
                    repo,
                    ref=ref,
                    allowed_docs=allowed_docs,
                    page_cache=page_cache,
                )
                if validated is None:
                    continue

                key = validated["dedupe_key"]
                evidence = local_evidence.get(key)
                if evidence is None:
                    evidence = repo.find_exact_evidence(
                        document_id=validated["document_id"],
                        document_page_id=validated["document_page_id"],
                        page_no=validated["page_no"],
                        quote_text=validated["quote_text"],
                        char_start=validated["char_start"],
                        char_end=validated["char_end"],
                        extraction_method=validated["extraction_method"],
                    )
                    if evidence is None:
                        evidence = repo.add_evidence(
                            Evidence(
                                document_id=validated["document_id"],
                                document_page_id=validated["document_page_id"],
                                page_no=validated["page_no"],
                                quote_text=validated["quote_text"],
                                bbox_json=None,
                                char_start=validated["char_start"],
                                char_end=validated["char_end"],
                                extraction_method=validated["extraction_method"],
                                confidence=None,
                            )
                        )
                        evidence_count += 1
                    local_evidence[key] = evidence

                pair = (diff.id, evidence.id)
                if pair not in diff_pairs:
                    repo.add_diff_evidence(diff_item_id=diff.id, evidence_id=evidence.id)
                    diff_pairs.add(pair)
                    diff_evidence_count += 1

                if not allow_links or base_target is None:
                    continue

                if work.require_project_relation is not None:
                    rel_field, code = work.require_project_relation
                    if not repo.project_has_relation(
                        project_id=base_target.target_id,
                        relation_field=rel_field,
                        code=code,
                    ):
                        continue
                    link_field = work.link_field_override
                else:
                    link_field = base_target.field_name

                link_key = (
                    evidence.id,
                    base_target.target_type,
                    base_target.target_id,
                    link_field,
                    RELATION_TYPE_SUPPORTS,
                )
                if link_key in link_keys:
                    continue
                existing_link = repo.find_link(
                    evidence_id=evidence.id,
                    target_type=base_target.target_type,
                    target_id=base_target.target_id,
                    field_name=link_field,
                    relation_type=RELATION_TYPE_SUPPORTS,
                )
                if existing_link is None:
                    repo.add_link(
                        EvidenceLink(
                            evidence_id=evidence.id,
                            target_type=base_target.target_type,
                            target_id=base_target.target_id,
                            field_name=link_field,
                            relation_type=RELATION_TYPE_SUPPORTS,
                        )
                    )
                    link_count += 1
                link_keys.add(link_key)

    logger.info(
        "evidence materialize analysis_id=%s evidence_count=%s link_count=%s "
        "diff_evidence_count=%s",
        run.id,
        evidence_count,
        link_count,
        diff_evidence_count,
    )
    return EvidenceMaterializeResult(
        evidence_count=evidence_count,
        evidence_link_count=link_count,
        diff_evidence_count=diff_evidence_count,
    )


def _validate_source_ref(
    repo: EvidenceRepository,
    *,
    ref: dict[str, Any],
    allowed_docs: set[UUID],
    page_cache: dict[tuple[UUID, int], DocumentPage | None],
) -> dict[str, Any] | None:
    raw_doc = ref.get("document_id")
    try:
        document_id = UUID(str(raw_doc))
    except (TypeError, ValueError):
        return None
    if document_id not in allowed_docs:
        return None

    try:
        page_no = int(ref.get("page_no"))
    except (TypeError, ValueError):
        return None
    if page_no <= 0:
        return None

    quote = ref.get("quote_text")
    if not isinstance(quote, str) or not quote.strip():
        return None
    quote_text = quote.strip()

    cache_key = (document_id, page_no)
    if cache_key not in page_cache:
        page_cache[cache_key] = repo.get_page(document_id, page_no)
    page = page_cache[cache_key]
    if page is None:
        return None

    extracted = page.extracted_text or ""
    if quote_text in extracted:
        char_start = extracted.index(quote_text)
        char_end = char_start + len(quote_text)
        extraction_method = page.extraction_method or "TEXT_PARSER"
    else:
        # Prompt-normalized quote absent from parser text → VLM provenance.
        char_start = None
        char_end = None
        extraction_method = "VLM"

    return {
        "document_id": document_id,
        "document_page_id": page.id,
        "page_no": page_no,
        "quote_text": quote_text,
        "char_start": char_start,
        "char_end": char_end,
        "extraction_method": extraction_method,
        "dedupe_key": (
            document_id,
            page.id,
            page_no,
            quote_text,
            char_start,
            char_end,
            extraction_method,
        ),
    }


def _resolve_existing_target(
    db: Session,
    *,
    diff: AnalysisDiffItem,
    person_id: UUID,
) -> AppliedEvidenceTarget | None:
    """Deterministic resolver for SAME / non-registry targets (no FOR UPDATE)."""
    entity = diff.entity_type

    if entity == "PROFILE":
        if not diff.field_name:
            return None
        return AppliedEvidenceTarget(
            diff_id=diff.id,
            target_type="PERSON_PROFILE",
            target_id=person_id,
            field_name=diff.field_name,
        )

    if entity == "JOB":
        data = _as_dict_loose(diff.new_value)
        code = _code_from(data)
        job_type = str(data.get("job_type") or "PRIMARY").strip().upper() or "PRIMARY"
        if not code:
            return None
        row = db.execute(
            select(PersonJob).where(
                PersonJob.person_id == person_id,
                PersonJob.job_code == code,
                PersonJob.job_type == job_type,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return AppliedEvidenceTarget(
            diff_id=diff.id,
            target_type="PERSON_JOB",
            target_id=row.id,
            field_name="job_code",
        )

    if entity == "TECH":
        data = _as_dict_loose(diff.new_value)
        code = _code_from(data)
        if not code:
            return None
        row = db.execute(
            select(PersonSkill).where(
                PersonSkill.person_id == person_id,
                PersonSkill.tech_code == code,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return AppliedEvidenceTarget(
            diff_id=diff.id,
            target_type="PERSON_SKILL",
            target_id=row.id,
            field_name="tech_code",
        )

    if entity == "EXP":
        data = _as_dict_loose(diff.new_value)
        code = _code_from(data)
        if not code:
            return None
        row = db.execute(
            select(PersonExpertise).where(
                PersonExpertise.person_id == person_id,
                PersonExpertise.exp_code == code,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return AppliedEvidenceTarget(
            diff_id=diff.id,
            target_type="PERSON_EXPERTISE",
            target_id=row.id,
            field_name="exp_code",
        )

    if entity == "EMPLOYMENT":
        return _career_target(diff, person_id, "EMPLOYMENT_HISTORY", EmploymentHistory, db)
    if entity == "EDUCATION":
        return _career_target(diff, person_id, "EDUCATION", Education, db)
    if entity == "CERTIFICATION":
        return _career_target(diff, person_id, "CERTIFICATION", Certification, db)

    if entity == "PROJECT":
        target_id = diff.existing_target_id
        if target_id is None:
            return None
        project = db.execute(
            select(Project).where(
                Project.id == target_id,
                Project.person_id == person_id,
                Project.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if project is None:
            return None
        field_name = diff.field_name
        if field_name in _PROJECT_RELATION_FIELDS:
            code = _code_from(_as_dict_loose(diff.new_value))
            if not code:
                return None
            field_name = project_relation_field_name(field_name, code)
        elif field_name is None or field_name == "":
            field_name = None
        return AppliedEvidenceTarget(
            diff_id=diff.id,
            target_type="PROJECT",
            target_id=project.id,
            field_name=field_name,
        )

    return None


def _career_target(
    diff: AnalysisDiffItem,
    person_id: UUID,
    target_type: str,
    model: type,
    db: Session,
) -> AppliedEvidenceTarget | None:
    target_id = diff.existing_target_id
    if target_id is None:
        return None
    row = db.execute(select(model).where(model.id == target_id)).scalar_one_or_none()
    if row is None or getattr(row, "person_id", None) != person_id:
        return None
    return AppliedEvidenceTarget(
        diff_id=diff.id,
        target_type=target_type,
        target_id=row.id,
        field_name=diff.field_name,
    )


def _as_dict_loose(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"code": value}
    return {}


def _code_from(data: dict[str, Any]) -> str | None:
    for key in (
        "code",
        "job_code",
        "tech_code",
        "exp_code",
        "biz_code",
        "customer_type_code",
    ):
        raw = data.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return None
