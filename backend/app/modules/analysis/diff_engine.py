"""Diff Candidate Profile against Confirmed Profile revision snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.ai.schemas.profile_candidate import ProfileCandidateDocument

# Profile scalar fields compared against snapshot["profile"].
PROFILE_SCALAR_FIELDS: tuple[str, ...] = (
    "name",
    "birth_year",
    "phone",
    "email",
    "address_region",
    "affiliation_company",
    "department",
    "current_title",
    "employment_type",
    "technical_grade",
    "career_start_date",
    "career_document_value",
    "profile_summary",
)

EMPLOYMENT_FIELDS: tuple[str, ...] = (
    "company_name",
    "department",
    "title",
    "start_date",
    "end_date",
    "responsibilities",
)

EDUCATION_FIELDS: tuple[str, ...] = (
    "school_name",
    "major",
    "degree",
    "start_date",
    "end_date",
    "status",
)

CERTIFICATION_FIELDS: tuple[str, ...] = (
    "certification_name",
    "issuer",
    "acquired_date",
    "expiry_date",
)

PROJECT_COMPARE_FIELDS: tuple[str, ...] = (
    "project_name",
    "customer_name",
    "start_date",
    "end_date",
    "duration_months",
    "responsibilities",
    "project_summary",
)


@dataclass
class DiffSpec:
    entity_type: str
    change_type: str
    candidate_path: str | None = None
    existing_target_id: UUID | None = None
    field_name: str | None = None
    old_value: Any = None
    new_value: Any = None
    confidence: Decimal | float | None = None
    evidence_type: str | None = None
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    review_status: str = "PENDING"

    def to_persist_dict(self) -> dict[str, Any]:
        new_value = self.new_value
        # Keep lightweight source_refs discoverable on dict new_values.
        if self.source_refs and isinstance(new_value, dict):
            new_value = {**new_value, "source_refs": self.source_refs}
        elif self.source_refs and not isinstance(new_value, dict):
            new_value = {
                "value": new_value,
                "source_refs": self.source_refs,
            }
        return {
            "entity_type": self.entity_type,
            "candidate_path": self.candidate_path,
            "existing_target_id": self.existing_target_id,
            "field_name": self.field_name,
            "change_type": self.change_type,
            "old_value": self.old_value,
            "new_value": new_value,
            "confidence": self.confidence,
            "evidence_type": self.evidence_type,
            "review_status": self.review_status,
        }


def _eq(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


def _norm_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _refs_dump(refs: Any) -> list[dict[str, Any]]:
    if not refs:
        return []
    out: list[dict[str, Any]] = []
    for ref in refs:
        if hasattr(ref, "model_dump"):
            out.append(ref.model_dump(mode="json"))
        elif isinstance(ref, dict):
            out.append(dict(ref))
    return out


def _skill_meta(row: dict[str, Any] | Any) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        data = row.model_dump(mode="json")
    else:
        data = dict(row)
    return {
        "last_used_year": data.get("last_used_year"),
        "experience_months": data.get("experience_months"),
        "is_representative": bool(data.get("is_representative", False)),
    }


def build_diffs(
    candidate: ProfileCandidateDocument,
    base_snapshot: dict[str, Any] | None,
) -> list[DiffSpec]:
    """Build AnalysisDiffItem specs. Never emits REMOVE_CANDIDATE."""
    snap = base_snapshot or {}
    specs: list[DiffSpec] = []
    specs.extend(_diff_profile_scalars(candidate, snap.get("profile") or {}))
    specs.extend(_diff_jobs(candidate, snap.get("jobs") or []))
    specs.extend(_diff_skills(candidate, snap.get("skills") or []))
    specs.extend(_diff_expertise(candidate, snap.get("expertise") or []))
    specs.extend(
        _diff_keyed_records(
            entity_type="EMPLOYMENT",
            candidate_rows=candidate.employment_history,
            existing_rows=snap.get("employment_history") or [],
            path_prefix="employment_history",
            fields=EMPLOYMENT_FIELDS,
            natural_key=lambda r: (
                _norm_str(r.get("company_name") if isinstance(r, dict) else r.company_name),
                _norm_str(r.get("start_date") if isinstance(r, dict) else r.start_date),
                _norm_str(r.get("end_date") if isinstance(r, dict) else r.end_date),
            ),
            dump_row=_dump_model,
        )
    )
    specs.extend(
        _diff_keyed_records(
            entity_type="EDUCATION",
            candidate_rows=candidate.education,
            existing_rows=snap.get("education") or [],
            path_prefix="education",
            fields=EDUCATION_FIELDS,
            natural_key=lambda r: (
                _norm_str(r.get("school_name") if isinstance(r, dict) else r.school_name),
                _norm_str(r.get("degree") if isinstance(r, dict) else r.degree),
                _norm_str(r.get("start_date") if isinstance(r, dict) else r.start_date),
                _norm_str(r.get("end_date") if isinstance(r, dict) else r.end_date),
            ),
            dump_row=_dump_model,
        )
    )
    specs.extend(
        _diff_keyed_records(
            entity_type="CERTIFICATION",
            candidate_rows=candidate.certifications,
            existing_rows=snap.get("certifications") or [],
            path_prefix="certifications",
            fields=CERTIFICATION_FIELDS,
            natural_key=lambda r: (
                _norm_str(
                    r.get("certification_name")
                    if isinstance(r, dict)
                    else r.certification_name
                ),
                _norm_str(r.get("issuer") if isinstance(r, dict) else r.issuer),
                _norm_str(
                    r.get("acquired_date") if isinstance(r, dict) else r.acquired_date
                ),
            ),
            dump_row=_dump_model,
        )
    )
    specs.extend(_diff_projects(candidate, snap.get("projects") or []))
    return specs


def _dump_model(row: Any) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    return dict(row)


def _diff_profile_scalars(
    candidate: ProfileCandidateDocument, profile_snap: dict[str, Any]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    cand = candidate.profile.model_dump(mode="json")
    for field_name in PROFILE_SCALAR_FIELDS:
        new_val = cand.get(field_name)
        if new_val is None or new_val == "":
            continue
        old_val = profile_snap.get(field_name)
        path = f"profile.{field_name}"
        if _eq(old_val, new_val):
            change = "SAME"
        elif old_val is None or old_val == "":
            change = "NEW"
        else:
            # Never auto-UPDATE scalars when both sides are non-null.
            change = "CONFLICT"
        specs.append(
            DiffSpec(
                entity_type="PROFILE",
                candidate_path=path,
                field_name=field_name,
                change_type=change,
                old_value=old_val,
                new_value=new_val,
            )
        )
    return specs


def _diff_jobs(
    candidate: ProfileCandidateDocument, existing: list[dict[str, Any]]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    existing_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing:
        code = _norm_str(row.get("job_code") or row.get("code"))
        job_type = _norm_str(row.get("job_type")) or "PRIMARY"
        if code:
            existing_by_key[(code, job_type)] = row

    for idx, job in enumerate(candidate.jobs):
        path = f"jobs[{idx}]"
        refs = _refs_dump(job.source_refs)
        dump = job.model_dump(mode="json")
        code = _norm_str(job.code)
        job_type = _norm_str(job.job_type) or "PRIMARY"
        if not code:
            specs.append(
                DiffSpec(
                    entity_type="JOB",
                    candidate_path=path,
                    change_type="REVIEW",
                    new_value=dump,
                    confidence=job.confidence,
                    source_refs=refs,
                )
            )
            continue
        key = (code, job_type)
        old = existing_by_key.get(key)
        if old is None:
            specs.append(
                DiffSpec(
                    entity_type="JOB",
                    candidate_path=path,
                    change_type="NEW",
                    new_value=dump,
                    confidence=job.confidence,
                    source_refs=refs,
                )
            )
        else:
            specs.append(
                DiffSpec(
                    entity_type="JOB",
                    candidate_path=path,
                    change_type="SAME",
                    old_value=old,
                    new_value=dump,
                    confidence=job.confidence,
                    source_refs=refs,
                )
            )
    return specs


def _diff_skills(
    candidate: ProfileCandidateDocument, existing: list[dict[str, Any]]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    existing_by_code: dict[str, dict[str, Any]] = {}
    for row in existing:
        code = _norm_str(row.get("tech_code") or row.get("code"))
        if code:
            existing_by_code[code] = row

    for idx, skill in enumerate(candidate.skills):
        path = f"skills[{idx}]"
        refs = _refs_dump(skill.source_refs)
        dump = skill.model_dump(mode="json")
        code = _norm_str(skill.code)
        if not code:
            specs.append(
                DiffSpec(
                    entity_type="TECH",
                    candidate_path=path,
                    change_type="REVIEW",
                    new_value=dump,
                    confidence=skill.confidence,
                    source_refs=refs,
                )
            )
            continue
        old = existing_by_code.get(code)
        if old is None:
            specs.append(
                DiffSpec(
                    entity_type="TECH",
                    candidate_path=path,
                    change_type="NEW",
                    new_value=dump,
                    confidence=skill.confidence,
                    source_refs=refs,
                )
            )
            continue
        if _skill_meta(old) == _skill_meta(skill):
            change = "SAME"
        else:
            change = "UPDATE"
        specs.append(
            DiffSpec(
                entity_type="TECH",
                candidate_path=path,
                change_type=change,
                old_value=old,
                new_value=dump,
                confidence=skill.confidence,
                source_refs=refs,
            )
        )
    return specs


def _diff_expertise(
    candidate: ProfileCandidateDocument, existing: list[dict[str, Any]]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    existing_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing:
        code = _norm_str(row.get("exp_code") or row.get("code"))
        evidence = _norm_str(row.get("evidence_type")) or "EXPLICIT"
        if code:
            existing_by_key[(code, evidence)] = row

    for idx, exp in enumerate(candidate.expertise):
        path = f"expertise[{idx}]"
        refs = _refs_dump(exp.source_refs)
        dump = exp.model_dump(mode="json")
        code = _norm_str(exp.code)
        evidence = _norm_str(exp.evidence_type) or "EXPLICIT"
        if not code:
            specs.append(
                DiffSpec(
                    entity_type="EXP",
                    candidate_path=path,
                    change_type="REVIEW",
                    evidence_type=evidence,
                    new_value=dump,
                    confidence=exp.confidence,
                    source_refs=refs,
                )
            )
            continue
        old = existing_by_key.get((code, evidence))
        if old is None:
            # Same code different evidence_type → still NEW relation row.
            specs.append(
                DiffSpec(
                    entity_type="EXP",
                    candidate_path=path,
                    change_type="NEW",
                    evidence_type=evidence,
                    new_value=dump,
                    confidence=exp.confidence,
                    source_refs=refs,
                )
            )
        else:
            specs.append(
                DiffSpec(
                    entity_type="EXP",
                    candidate_path=path,
                    change_type="SAME",
                    evidence_type=evidence,
                    old_value=old,
                    new_value=dump,
                    confidence=exp.confidence,
                    source_refs=refs,
                )
            )
    return specs


def _diff_keyed_records(
    *,
    entity_type: str,
    candidate_rows: list[Any],
    existing_rows: list[dict[str, Any]],
    path_prefix: str,
    fields: tuple[str, ...],
    natural_key,
    dump_row,
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in existing_rows:
        key = natural_key(row)
        if key[0] is None:
            continue
        grouped.setdefault(key, []).append(row)

    for idx, cand in enumerate(candidate_rows):
        path = f"{path_prefix}[{idx}]"
        dump = dump_row(cand)
        refs = _refs_dump(getattr(cand, "source_refs", None) or dump.get("source_refs"))
        key = natural_key(cand)
        if key[0] is None:
            specs.append(
                DiffSpec(
                    entity_type=entity_type,
                    candidate_path=path,
                    change_type="REVIEW",
                    new_value=dump,
                    confidence=getattr(cand, "confidence", None),
                    source_refs=refs,
                )
            )
            continue
        matches = grouped.get(key, [])
        if len(matches) > 1:
            specs.append(
                DiffSpec(
                    entity_type=entity_type,
                    candidate_path=path,
                    change_type="REVIEW",
                    old_value=matches,
                    new_value=dump,
                    confidence=getattr(cand, "confidence", None),
                    source_refs=refs,
                )
            )
            continue
        if not matches:
            specs.append(
                DiffSpec(
                    entity_type=entity_type,
                    candidate_path=path,
                    change_type="NEW",
                    new_value=dump,
                    confidence=getattr(cand, "confidence", None),
                    source_refs=refs,
                )
            )
            continue

        old = matches[0]
        target_id = _as_uuid(old.get("id"))
        field_changes = _compare_fields(old, dump, fields)
        if not field_changes:
            specs.append(
                DiffSpec(
                    entity_type=entity_type,
                    candidate_path=path,
                    existing_target_id=target_id,
                    change_type="SAME",
                    old_value=old,
                    new_value=dump,
                    confidence=getattr(cand, "confidence", None),
                    source_refs=refs,
                )
            )
            continue

        # Emit one item per differing field for clarity in review UI.
        for field_name, change, old_v, new_v in field_changes:
            specs.append(
                DiffSpec(
                    entity_type=entity_type,
                    candidate_path=f"{path}.{field_name}",
                    existing_target_id=target_id,
                    field_name=field_name,
                    change_type=change,
                    old_value=old_v,
                    new_value=new_v,
                    confidence=getattr(cand, "confidence", None),
                    source_refs=refs,
                )
            )
    return specs


def _compare_fields(
    old: dict[str, Any], new: dict[str, Any], fields: tuple[str, ...]
) -> list[tuple[str, str, Any, Any]]:
    """Return (field, change_type, old, new) for differing fields.

    UPDATE = null-fill (old null, new present). CONFLICT = both non-null differ.
    """
    out: list[tuple[str, str, Any, Any]] = []
    for field_name in fields:
        old_v = old.get(field_name)
        new_v = new.get(field_name)
        if new_v is None or new_v == "":
            continue
        if _eq(old_v, new_v):
            continue
        if old_v is None or old_v == "":
            out.append((field_name, "UPDATE", old_v, new_v))
        else:
            out.append((field_name, "CONFLICT", old_v, new_v))
    return out


def _project_match_key(row: Any) -> tuple[str | None, str | None, str | None, str | None]:
    if isinstance(row, dict):
        return (
            _norm_str(row.get("project_name")),
            _norm_str(row.get("customer_name")),
            _norm_str(row.get("start_date")),
            _norm_str(row.get("end_date")),
        )
    return (
        _norm_str(row.project_name),
        _norm_str(row.customer_name),
        _norm_str(row.start_date),
        _norm_str(row.end_date),
    )


def _diff_projects(
    candidate: ProfileCandidateDocument, existing: list[dict[str, Any]]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for row in existing:
        key = _project_match_key(row)
        if key[0] is None:
            continue
        grouped.setdefault(key, []).append(row)

    for idx, project in enumerate(candidate.projects):
        path = f"projects[{idx}]"
        dump = project.model_dump(mode="json")
        refs = _refs_dump(project.source_refs)
        key = _project_match_key(project)
        if key[0] is None:
            specs.append(
                DiffSpec(
                    entity_type="PROJECT",
                    candidate_path=path,
                    change_type="REVIEW",
                    new_value=dump,
                    confidence=project.confidence,
                    source_refs=refs,
                )
            )
            continue

        matches = grouped.get(key, [])
        if len(matches) > 1:
            specs.append(
                DiffSpec(
                    entity_type="PROJECT",
                    candidate_path=path,
                    change_type="REVIEW",
                    old_value=matches,
                    new_value=dump,
                    confidence=project.confidence,
                    source_refs=refs,
                )
            )
            continue
        if not matches:
            # Same name (+ optional customer) but different dates → ambiguous REVIEW.
            similar = [
                row
                for row in existing
                if _norm_str(row.get("project_name")) == key[0]
                and (
                    key[1] is None
                    or _norm_str(row.get("customer_name")) is None
                    or _norm_str(row.get("customer_name")) == key[1]
                )
            ]
            if similar:
                specs.append(
                    DiffSpec(
                        entity_type="PROJECT",
                        candidate_path=path,
                        change_type="REVIEW",
                        old_value=similar if len(similar) > 1 else similar[0],
                        new_value=dump,
                        confidence=project.confidence,
                        source_refs=refs,
                    )
                )
            else:
                specs.append(
                    DiffSpec(
                        entity_type="PROJECT",
                        candidate_path=path,
                        change_type="NEW",
                        new_value=dump,
                        confidence=project.confidence,
                        source_refs=refs,
                    )
                )
            continue

        old = matches[0]
        target_id = _as_uuid(old.get("id"))
        # Exact name+customer+dates match — compare remaining fields.
        field_changes = _compare_fields(old, dump, PROJECT_COMPARE_FIELDS)
        # Also compare relation code sets at a coarse level.
        for rel_field in ("jobs", "skills", "expertise", "business_domains", "customer_types"):
            old_codes = _code_set(old.get(rel_field))
            new_codes = _code_set(dump.get(rel_field))
            if old_codes != new_codes:
                if not old_codes and new_codes:
                    field_changes.append((rel_field, "UPDATE", old.get(rel_field), dump.get(rel_field)))
                elif old_codes and new_codes:
                    field_changes.append(
                        (rel_field, "CONFLICT", old.get(rel_field), dump.get(rel_field))
                    )

        if not field_changes:
            specs.append(
                DiffSpec(
                    entity_type="PROJECT",
                    candidate_path=path,
                    existing_target_id=target_id,
                    change_type="SAME",
                    old_value=old,
                    new_value=dump,
                    confidence=project.confidence,
                    source_refs=refs,
                )
            )
            continue

        for field_name, change, old_v, new_v in field_changes:
            # Skip identity fields that already matched the key for SAME-ness.
            if field_name in {"project_name", "customer_name", "start_date", "end_date"}:
                if change == "SAME":
                    continue
            specs.append(
                DiffSpec(
                    entity_type="PROJECT",
                    candidate_path=f"{path}.{field_name}",
                    existing_target_id=target_id,
                    field_name=field_name,
                    change_type=change,
                    old_value=old_v,
                    new_value=new_v,
                    confidence=project.confidence,
                    source_refs=refs,
                )
            )
    return specs


def _code_set(rows: Any) -> set[str]:
    if not rows:
        return set()
    out: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            code = _norm_str(row.get("code") or row.get("job_code") or row.get("tech_code"))
        else:
            code = _norm_str(getattr(row, "code", None))
        if code:
            out.add(code)
    return out
