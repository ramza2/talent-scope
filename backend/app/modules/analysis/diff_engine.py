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
            similar_match=_employment_similar,
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
            similar_match=_education_similar,
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
            similar_match=_certification_similar,
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
    """Match Person EXP by exp_code only (DB unique is person_id + exp_code)."""
    specs: list[DiffSpec] = []
    existing_by_code: dict[str, dict[str, Any]] = {}
    for row in existing:
        code = _norm_str(row.get("exp_code") or row.get("code"))
        if code and code not in existing_by_code:
            existing_by_code[code] = row

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
        old = existing_by_code.get(code)
        if old is None:
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
            continue
        old_evidence = _norm_str(old.get("evidence_type")) or "EXPLICIT"
        if old_evidence == evidence:
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
        else:
            # Same code cannot become a second NEW relation — require review.
            specs.append(
                DiffSpec(
                    entity_type="EXP",
                    candidate_path=path,
                    change_type="REVIEW",
                    evidence_type=evidence,
                    old_value=old,
                    new_value=dump,
                    confidence=exp.confidence,
                    source_refs=refs,
                )
            )
    return specs


def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def parse_date_parts(value: Any) -> tuple[int, int | None, int | None] | None:
    """Parse conservative YEAR / YEAR-MONTH / YYYY-MM-DD (or ISO date) strings."""
    text = _norm_str(value)
    if not text:
        return None
    # Allow trailing time from ISO datetimes: 2020-03-01T00:00:00
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text.replace("/", "-")
    parts = text.split("-")
    if not parts or not parts[0].isdigit() or len(parts[0]) != 4:
        return None
    year = int(parts[0])
    if year < 1900 or year > 2100:
        return None
    month: int | None = None
    day: int | None = None
    if len(parts) >= 2 and parts[1].isdigit():
        month = int(parts[1])
        if month < 1 or month > 12:
            return None
    if len(parts) >= 3 and parts[2].isdigit():
        day = int(parts[2])
        if day < 1 or day > 31:
            return None
    if day is not None and month is None:
        return None
    return (year, month, day)


def dates_compatible(a: Any, b: Any) -> bool:
    """True when dates share a consistent prefix; None vs value is not 'equal' but OK for similarity.

    - both None → True (no conflicting date signal)
    - one None → True (missing precision / omitted end)
    - both present → True only if year matches and more-precise parts agree
    """
    if a is None or a == "":
        return True
    if b is None or b == "":
        return True
    pa = parse_date_parts(a)
    pb = parse_date_parts(b)
    if pa is None or pb is None:
        # Unparseable: only exact string equality counts as compatible.
        return _eq(a, b)
    ya, ma, da = pa
    yb, mb, db = pb
    if ya != yb:
        return False
    if ma is not None and mb is not None and ma != mb:
        return False
    if da is not None and db is not None and da != db:
        return False
    return True


def _optional_str_compatible(a: Any, b: Any) -> bool:
    na = _norm_str(a)
    nb = _norm_str(b)
    if na is None or nb is None:
        return True
    return na == nb


def _employment_similar(cand: Any, existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    company = _norm_str(_field(cand, "company_name"))
    if not company:
        return []
    out: list[dict[str, Any]] = []
    for row in existing_rows:
        if _norm_str(row.get("company_name")) != company:
            continue
        if not dates_compatible(_field(cand, "start_date"), row.get("start_date")):
            continue
        if not dates_compatible(_field(cand, "end_date"), row.get("end_date")):
            continue
        out.append(row)
    return out


def _education_similar(cand: Any, existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    school = _norm_str(_field(cand, "school_name"))
    if not school:
        return []
    out: list[dict[str, Any]] = []
    for row in existing_rows:
        if _norm_str(row.get("school_name")) != school:
            continue
        if not _optional_str_compatible(_field(cand, "degree"), row.get("degree")):
            continue
        if not dates_compatible(_field(cand, "start_date"), row.get("start_date")):
            continue
        if not dates_compatible(_field(cand, "end_date"), row.get("end_date")):
            continue
        out.append(row)
    return out


def _certification_similar(
    cand: Any, existing_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    name = _norm_str(_field(cand, "certification_name"))
    if not name:
        return []
    out: list[dict[str, Any]] = []
    for row in existing_rows:
        if _norm_str(row.get("certification_name")) != name:
            continue
        if not _optional_str_compatible(_field(cand, "issuer"), row.get("issuer")):
            continue
        if not dates_compatible(_field(cand, "acquired_date"), row.get("acquired_date")):
            continue
        out.append(row)
    return out


def _diff_keyed_records(
    *,
    entity_type: str,
    candidate_rows: list[Any],
    existing_rows: list[dict[str, Any]],
    path_prefix: str,
    fields: tuple[str, ...],
    natural_key,
    dump_row,
    similar_match=None,
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
            similar = similar_match(cand, existing_rows) if similar_match else []
            if not similar:
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
            elif len(similar) == 1:
                specs.append(
                    DiffSpec(
                        entity_type=entity_type,
                        candidate_path=path,
                        existing_target_id=_as_uuid(similar[0].get("id")),
                        change_type="REVIEW",
                        old_value=similar[0],
                        new_value=dump,
                        confidence=getattr(cand, "confidence", None),
                        source_refs=refs,
                    )
                )
            else:
                specs.append(
                    DiffSpec(
                        entity_type=entity_type,
                        candidate_path=path,
                        change_type="REVIEW",
                        old_value=similar,
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


PROJECT_RELATION_FIELDS: tuple[str, ...] = (
    "jobs",
    "skills",
    "expertise",
    "business_domains",
    "customer_types",
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
            specs.extend(
                _diff_project_relations(
                    path=path,
                    old_project=None,
                    new_project=dump,
                    existing_target_id=None,
                    confidence=project.confidence,
                    source_refs=refs,
                    additions_only_unmapped=True,
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
                # Surface unmapped relation items on NEW projects for review.
                specs.extend(
                    _diff_project_relations(
                        path=path,
                        old_project=None,
                        new_project=dump,
                        existing_target_id=None,
                        confidence=project.confidence,
                        source_refs=refs,
                        additions_only_unmapped=True,
                    )
                )
            continue

        old = matches[0]
        target_id = _as_uuid(old.get("id"))
        field_changes = _compare_fields(old, dump, PROJECT_COMPARE_FIELDS)
        relation_specs = _diff_project_relations(
            path=path,
            old_project=old,
            new_project=dump,
            existing_target_id=target_id,
            confidence=project.confidence,
            source_refs=refs,
        )

        if not field_changes and not relation_specs:
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
        specs.extend(relation_specs)
    return specs


def _relation_item_code(row: dict[str, Any]) -> str | None:
    return _norm_str(
        row.get("code")
        or row.get("job_code")
        or row.get("tech_code")
        or row.get("exp_code")
        or row.get("biz_code")
        or row.get("customer_type_code")
    )


def _as_relation_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    if isinstance(row, dict):
        return dict(row)
    return {}


def _diff_project_relations(
    *,
    path: str,
    old_project: dict[str, Any] | None,
    new_project: dict[str, Any],
    existing_target_id: UUID | None,
    confidence: Any,
    source_refs: list[dict[str, Any]],
    additions_only_unmapped: bool = False,
) -> list[DiffSpec]:
    """Additive Project relation Diffs — never emit removal for omitted codes."""
    specs: list[DiffSpec] = []
    for rel_field in PROJECT_RELATION_FIELDS:
        existing_rows = (old_project or {}).get(rel_field) or []
        candidate_rows = new_project.get(rel_field) or []
        existing_codes = {
            code
            for row in existing_rows
            if (code := _relation_item_code(_as_relation_dict(row))) is not None
        }

        for idx, raw_item in enumerate(candidate_rows):
            item = _as_relation_dict(raw_item)
            code = _relation_item_code(item)
            raw_value = _norm_str(item.get("raw_value") or item.get("name"))
            item_path = f"{path}.{rel_field}[{idx}]"

            if code is None:
                if raw_value:
                    specs.append(
                        DiffSpec(
                            entity_type="PROJECT",
                            candidate_path=item_path,
                            existing_target_id=existing_target_id,
                            field_name=rel_field,
                            change_type="REVIEW",
                            new_value=item,
                            confidence=confidence,
                            source_refs=source_refs,
                        )
                    )
                continue

            if additions_only_unmapped:
                continue

            if code in existing_codes:
                continue

            # Valid new code relative to confirmed project → additive UPDATE.
            new_value = {
                key: value
                for key, value in item.items()
                if key in {"raw_value", "code", "evidence_type", "name"}
                and value is not None
                and value != ""
            }
            if "code" not in new_value:
                new_value["code"] = code
            specs.append(
                DiffSpec(
                    entity_type="PROJECT",
                    candidate_path=item_path,
                    existing_target_id=existing_target_id,
                    field_name=rel_field,
                    change_type="UPDATE",
                    new_value=new_value,
                    confidence=confidence,
                    source_refs=source_refs,
                )
            )
    return specs


def _code_set(rows: Any) -> set[str]:
    if not rows:
        return set()
    out: set[str] = set()
    for row in rows:
        code = _relation_item_code(_as_relation_dict(row))
        if code:
            out.add(code)
    return out
