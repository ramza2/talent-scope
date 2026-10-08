"""Read-only AnalysisRun quality metrics (no DB writes, no AI calls).

Evidence-eligible entities are those that carry ``source_refs`` in the
profile-candidate-v1 schema produced by compact expand / LLM normalize:

- filled profile scalars in ``PROFILE_SCALAR_FIELDS`` (via ``profile.source_refs`` map)
- each item in jobs / skills / expertise / employment_history / education /
  certifications / projects
- each nested code-ref item under a project (jobs/skills/expertise/
  business_domains/customer_types)

Profile containers, ``summary``, and ``analysis`` metadata are not denominators.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.ai.schemas.profile_candidate import PROFILE_SCALAR_FIELDS

# Output entity keys (employment maps from storage key employment_history).
ENTITY_COUNT_KEYS: tuple[str, ...] = (
    "jobs",
    "skills",
    "expertise",
    "projects",
    "employment",
    "education",
    "certifications",
)

_STORAGE_LIST_KEYS: dict[str, str] = {
    "jobs": "jobs",
    "skills": "skills",
    "expertise": "expertise",
    "projects": "projects",
    "employment": "employment_history",
    "education": "education",
    "certifications": "certifications",
}

_PROJECT_NESTED_REF_KEYS: tuple[str, ...] = (
    "jobs",
    "skills",
    "expertise",
    "business_domains",
    "customer_types",
)

CHANGE_TYPES: tuple[str, ...] = ("SAME", "NEW", "UPDATE", "CONFLICT", "REVIEW")


@dataclass(frozen=True)
class EvidenceMetrics:
    evidence_eligible_count: int = 0
    with_source_refs_count: int = 0
    source_ref_count: int = 0
    evidence_coverage_pct: float | None = None
    refs_with_document_id: int = 0
    refs_with_quote_text: int = 0
    refs_with_page_no: int = 0
    valid_document_ref_count: int = 0
    invalid_document_ref_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiffSummary:
    same: int = 0
    new: int = 0
    update: int = 0
    conflict: int = 0
    review: int = 0
    pending: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunQualityReport:
    analysis_run_id: str
    person_id: str
    person_name: str | None
    status: str
    document_count: int
    documents: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str | None = None
    schema_version: str | None = None
    llm_model: str | None = None
    vlm_model: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    entity_counts: dict[str, int] = field(default_factory=dict)
    evidence: EvidenceMetrics = field(default_factory=EvidenceMetrics)
    diffs: DiffSummary = field(default_factory=DiffSummary)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "person_id": self.person_id,
            "person_name": self.person_name,
            "status": self.status,
            "document_count": self.document_count,
            "documents": list(self.documents),
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "llm_model": self.llm_model,
            "vlm_model": self.vlm_model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "entity_counts": dict(self.entity_counts),
            "evidence": self.evidence.to_dict(),
            "diffs": self.diffs.to_dict(),
            "error_message": self.error_message,
        }


@dataclass
class AggregateSummary:
    run_count: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    avg_duration_seconds: float | None = None
    total_document_count: int = 0
    entity_count_totals: dict[str, int] = field(default_factory=dict)
    evidence_eligible_count: int = 0
    with_source_refs_count: int = 0
    evidence_coverage_pct: float | None = None
    source_ref_count: int = 0
    refs_with_document_id: int = 0
    refs_with_quote_text: int = 0
    refs_with_page_no: int = 0
    valid_document_ref_count: int = 0
    invalid_document_ref_count: int = 0
    diff_totals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scalar_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float, bool)):
        return True
    return False


def count_entities(candidate_json: Any) -> dict[str, int]:
    """Count list entities using storage keys; output uses report keys."""
    out = {key: 0 for key in ENTITY_COUNT_KEYS}
    if not isinstance(candidate_json, dict):
        return out
    for report_key, storage_key in _STORAGE_LIST_KEYS.items():
        items = candidate_json.get(storage_key)
        if isinstance(items, list):
            out[report_key] = sum(1 for item in items if isinstance(item, dict))
    return out


def duration_seconds(
    started_at: datetime | None, completed_at: datetime | None
) -> float | None:
    if started_at is None or completed_at is None:
        return None
    try:
        delta = completed_at - started_at
    except TypeError:
        return None
    return round(delta.total_seconds(), 3)


def _normalize_ref(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return item


def _iter_source_refs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        ref = _normalize_ref(item)
        if ref is not None:
            out.append(ref)
    return out


def _coverage_pct(with_refs: int, eligible: int) -> float | None:
    if eligible <= 0:
        return None
    return round((with_refs / eligible) * 100.0, 2)


def _accumulate_refs(
    refs: list[dict[str, Any]],
    *,
    valid_document_ids: set[str],
    metrics: dict[str, int],
) -> None:
    metrics["source_ref_count"] += len(refs)
    for ref in refs:
        doc_id = ref.get("document_id")
        if isinstance(doc_id, str) and doc_id.strip():
            metrics["refs_with_document_id"] += 1
            if doc_id in valid_document_ids:
                metrics["valid_document_ref_count"] += 1
            else:
                metrics["invalid_document_ref_count"] += 1
        quote = ref.get("quote_text")
        if isinstance(quote, str) and quote.strip():
            metrics["refs_with_quote_text"] += 1
        page_no = ref.get("page_no")
        if page_no is not None and not isinstance(page_no, bool):
            metrics["refs_with_page_no"] += 1


def _observe_entity(
    source_refs_raw: Any,
    *,
    valid_document_ids: set[str],
    metrics: dict[str, int],
) -> None:
    metrics["evidence_eligible_count"] += 1
    refs = _iter_source_refs(source_refs_raw)
    if refs:
        metrics["with_source_refs_count"] += 1
        _accumulate_refs(refs, valid_document_ids=valid_document_ids, metrics=metrics)


def collect_evidence_metrics(
    candidate_json: Any,
    *,
    valid_document_ids: set[str] | set[UUID] | None = None,
) -> EvidenceMetrics:
    """Aggregate source_refs quality without raising on malformed shapes."""
    valid_ids = {
        str(item) for item in (valid_document_ids or set()) if item is not None
    }
    metrics = {
        "evidence_eligible_count": 0,
        "with_source_refs_count": 0,
        "source_ref_count": 0,
        "refs_with_document_id": 0,
        "refs_with_quote_text": 0,
        "refs_with_page_no": 0,
        "valid_document_ref_count": 0,
        "invalid_document_ref_count": 0,
    }
    if not isinstance(candidate_json, dict):
        return EvidenceMetrics(**metrics, evidence_coverage_pct=None)

    profile = candidate_json.get("profile")
    if isinstance(profile, dict):
        refs_map = profile.get("source_refs")
        if not isinstance(refs_map, dict):
            refs_map = {}
        for field_name in PROFILE_SCALAR_FIELDS:
            if not _scalar_filled(profile.get(field_name)):
                continue
            _observe_entity(
                refs_map.get(field_name),
                valid_document_ids=valid_ids,
                metrics=metrics,
            )

    for storage_key in (
        "jobs",
        "skills",
        "expertise",
        "employment_history",
        "education",
        "certifications",
        "projects",
    ):
        items = candidate_json.get(storage_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            _observe_entity(
                item.get("source_refs"),
                valid_document_ids=valid_ids,
                metrics=metrics,
            )
            if storage_key != "projects":
                continue
            for nested_key in _PROJECT_NESTED_REF_KEYS:
                nested_items = item.get(nested_key)
                if not isinstance(nested_items, list):
                    continue
                for nested in nested_items:
                    if not isinstance(nested, dict):
                        continue
                    _observe_entity(
                        nested.get("source_refs"),
                        valid_document_ids=valid_ids,
                        metrics=metrics,
                    )

    return EvidenceMetrics(
        **metrics,
        evidence_coverage_pct=_coverage_pct(
            metrics["with_source_refs_count"],
            metrics["evidence_eligible_count"],
        ),
    )


def summarize_diffs(
    rows: list[tuple[str, str]] | list[Any],
) -> DiffSummary:
    """Summarize (change_type, review_status) pairs."""
    counts = {key.lower(): 0 for key in CHANGE_TYPES}
    pending = 0
    for row in rows:
        if isinstance(row, tuple) and len(row) >= 2:
            change_type, review_status = row[0], row[1]
        else:
            change_type = getattr(row, "change_type", None)
            review_status = getattr(row, "review_status", None)
        key = str(change_type or "").upper()
        if key in CHANGE_TYPES:
            counts[key.lower()] += 1
        if str(review_status or "").upper() == "PENDING":
            pending += 1
    return DiffSummary(
        same=counts["same"],
        new=counts["new"],
        update=counts["update"],
        conflict=counts["conflict"],
        review=counts["review"],
        pending=pending,
    )


def build_run_report(
    *,
    analysis_run_id: UUID | str,
    person_id: UUID | str,
    person_name: str | None,
    status: str,
    candidate_json: Any,
    documents: list[dict[str, Any]] | None = None,
    prompt_version: str | None = None,
    schema_version: str | None = None,
    llm_model: str | None = None,
    vlm_model: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    diff_rows: list[tuple[str, str]] | None = None,
    error_message: str | None = None,
) -> RunQualityReport:
    docs = list(documents or [])
    valid_ids = {
        str(doc.get("document_id"))
        for doc in docs
        if doc.get("document_id") is not None
    }
    return RunQualityReport(
        analysis_run_id=str(analysis_run_id),
        person_id=str(person_id),
        person_name=person_name,
        status=status,
        document_count=len(docs),
        documents=docs,
        prompt_version=prompt_version,
        schema_version=schema_version,
        llm_model=llm_model,
        vlm_model=vlm_model,
        started_at=started_at.isoformat() if started_at is not None else None,
        completed_at=completed_at.isoformat() if completed_at is not None else None,
        duration_seconds=duration_seconds(started_at, completed_at),
        entity_counts=count_entities(candidate_json),
        evidence=collect_evidence_metrics(
            candidate_json, valid_document_ids=valid_ids
        ),
        diffs=summarize_diffs(diff_rows or []),
        error_message=error_message,
    )


def aggregate_reports(reports: list[RunQualityReport]) -> AggregateSummary:
    status_counts: dict[str, int] = {}
    entity_totals = {key: 0 for key in ENTITY_COUNT_KEYS}
    diff_totals = {
        "same": 0,
        "new": 0,
        "update": 0,
        "conflict": 0,
        "review": 0,
        "pending": 0,
    }
    durations: list[float] = []
    eligible = 0
    with_refs = 0
    source_ref_count = 0
    refs_with_document_id = 0
    refs_with_quote_text = 0
    refs_with_page_no = 0
    valid_document_ref_count = 0
    invalid_document_ref_count = 0
    total_docs = 0

    for report in reports:
        status_counts[report.status] = status_counts.get(report.status, 0) + 1
        total_docs += report.document_count
        if report.duration_seconds is not None:
            durations.append(report.duration_seconds)
        for key in ENTITY_COUNT_KEYS:
            entity_totals[key] += int(report.entity_counts.get(key, 0))
        for key in diff_totals:
            diff_totals[key] += int(getattr(report.diffs, key, 0))
        ev = report.evidence
        eligible += ev.evidence_eligible_count
        with_refs += ev.with_source_refs_count
        source_ref_count += ev.source_ref_count
        refs_with_document_id += ev.refs_with_document_id
        refs_with_quote_text += ev.refs_with_quote_text
        refs_with_page_no += ev.refs_with_page_no
        valid_document_ref_count += ev.valid_document_ref_count
        invalid_document_ref_count += ev.invalid_document_ref_count

    avg_duration = (
        round(sum(durations) / len(durations), 3) if durations else None
    )
    return AggregateSummary(
        run_count=len(reports),
        status_counts=status_counts,
        avg_duration_seconds=avg_duration,
        total_document_count=total_docs,
        entity_count_totals=entity_totals,
        evidence_eligible_count=eligible,
        with_source_refs_count=with_refs,
        evidence_coverage_pct=_coverage_pct(with_refs, eligible),
        source_ref_count=source_ref_count,
        refs_with_document_id=refs_with_document_id,
        refs_with_quote_text=refs_with_quote_text,
        refs_with_page_no=refs_with_page_no,
        valid_document_ref_count=valid_document_ref_count,
        invalid_document_ref_count=invalid_document_ref_count,
        diff_totals=diff_totals,
    )


def validate_selectors(
    *,
    analysis_run_ids: list[UUID] | None,
    person_id: UUID | None,
    latest: int | None,
) -> str | None:
    """Return an error message when selectors are invalid; else None."""
    has_run = bool(analysis_run_ids)
    has_person = person_id is not None
    has_latest = latest is not None
    if not (has_run or has_person or has_latest):
        return (
            "At least one selector is required: "
            "--analysis-run-id, --person-id, or --latest"
        )
    if has_latest and latest is not None and latest < 1:
        return "--latest must be >= 1"
    return None
