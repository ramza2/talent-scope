"""Evidence materialization and API — Confirm-time provenance."""

from app.modules.evidence.materializer import (
    AppliedEvidenceTarget,
    AppliedTargetRegistry,
    EvidenceMaterializeResult,
    extract_source_refs,
    materialize_confirm_evidence,
    project_relation_field_name,
    should_create_evidence_link,
)
from app.modules.evidence.router import router
from app.modules.evidence.schemas import EVIDENCE_TARGET_TYPES

__all__ = [
    "AppliedEvidenceTarget",
    "AppliedTargetRegistry",
    "EVIDENCE_TARGET_TYPES",
    "EvidenceMaterializeResult",
    "extract_source_refs",
    "materialize_confirm_evidence",
    "project_relation_field_name",
    "router",
    "should_create_evidence_link",
]
