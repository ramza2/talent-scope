"""Diff Candidate Profile against Confirmed Profile revision snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.ai.schemas.profile_candidate import (
    PROFILE_SCALAR_FIELDS,
    ProfileCandidateDocument,
)
from app.core.person_name import person_name_match_key
from app.modules.analysis.career_document_months import (
    normalize_career_document_value_text,
    parse_career_document_months,
)
from app.modules.analysis.confirm_dates import (
    is_ongoing_end_marker,
    normalize_confirmed_date,
    parse_partial_date,
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
                _employment_end_key(
                    r.get("end_date") if isinstance(r, dict) else r.end_date
                ),
            ),
            dump_row=_dump_model,
            similar_match=_employment_similar,
            new_requires_review=employment_candidate_dates_need_review,
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


def _career_document_value_change(old_val: Any, new_val: Any) -> str:
    """career_document_value-only: semantic-equivalent cleanup → UPDATE.

    When both sides parse to the same months and ``new_val`` is already
    canonical duration text, emit UPDATE so a reviewer can replace a noisy
    confirmed string with the cleaned candidate. Otherwise CONFLICT.
    """
    old_months = parse_career_document_months(
        old_val if isinstance(old_val, str) else None
    )
    new_months = parse_career_document_months(
        new_val if isinstance(new_val, str) else None
    )
    if old_months is None or new_months is None:
        return "CONFLICT"
    if old_months != new_months:
        return "CONFLICT"
    if not isinstance(new_val, str):
        return "CONFLICT"
    if normalize_career_document_value_text(new_val) != new_val:
        return "CONFLICT"
    return "UPDATE"


def _diff_profile_scalars(
    candidate: ProfileCandidateDocument, profile_snap: dict[str, Any]
) -> list[DiffSpec]:
    specs: list[DiffSpec] = []
    cand = candidate.profile.model_dump(mode="json")
    profile_refs = candidate.profile.source_refs or {}
    for field_name in PROFILE_SCALAR_FIELDS:
        new_val = cand.get(field_name)
        if new_val is None or new_val == "":
            continue
        old_val = profile_snap.get(field_name)
        path = f"profile.{field_name}"
        if field_name == "name":
            old_key = person_name_match_key(
                old_val if isinstance(old_val, str) else None
            )
            new_key = person_name_match_key(
                new_val if isinstance(new_val, str) else None
            )
            if old_key is not None and old_key == new_key:
                change = "SAME"
            elif old_val is None or old_val == "":
                change = "NEW"
            else:
                change = "CONFLICT"
        elif _eq(old_val, new_val):
            change = "SAME"
        elif old_val is None or old_val == "":
            change = "NEW"
        elif field_name == "career_document_value":
            change = _career_document_value_change(old_val, new_val)
        else:
            # Never auto-UPDATE other scalars when both sides are non-null.
            change = "CONFLICT"
        specs.append(
            DiffSpec(
                entity_type="PROFILE",
                candidate_path=path,
                field_name=field_name,
                change_type=change,
                old_value=old_val,
                new_value=new_val,
                source_refs=_refs_dump(profile_refs.get(field_name)),
            )
        )
    derived = _derived_career_confirmed_months_diff(candidate, profile_snap)
    if derived is not None:
        specs.append(derived)
    return specs


def _derived_career_confirmed_months_diff(
    candidate: ProfileCandidateDocument, profile_snap: dict[str, Any]
) -> DiffSpec | None:
    """Derive career_confirmed_months Diff from parseable career_document_value.

    Not part of LLM ProfileCandidate schema. Reuses career_document_value
    source_refs. Never overwrites an existing confirmed value automatically.
    """
    raw_value = candidate.profile.career_document_value
    months = parse_career_document_months(
        raw_value if isinstance(raw_value, str) else None
    )
    if months is None:
        return None

    old_val = profile_snap.get("career_confirmed_months")
    if old_val is None or old_val == "":
        change = "NEW"
        old_out: Any = None
    elif _eq(old_val, months):
        change = "SAME"
        old_out = old_val
    else:
        # Existing non-null confirmed months: CONFLICT, never auto-UPDATE.
        change = "CONFLICT"
        old_out = old_val

    profile_refs = candidate.profile.source_refs or {}
    return DiffSpec(
        entity_type="PROFILE",
        candidate_path="profile.career_confirmed_months",
        field_name="career_confirmed_months",
        change_type=change,
        old_value=old_out,
        new_value=months,
        source_refs=_refs_dump(profile_refs.get("career_document_value")),
    )


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

    Ongoing employment markers (``현재`` etc.) are NOT handled here — use
    ``_employment_end_dates_compatible`` for EMPLOYMENT.end_date only.
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


def _employment_end_key(value: Any) -> str | None:
    """Natural-key helper: ongoing end markers collapse to null."""
    if is_ongoing_end_marker(value):
        return None
    return _norm_str(value)


def _employment_end_dates_compatible(a: Any, b: Any) -> bool:
    """EMPLOYMENT.end_date similarity: ongoing markers act like omitted end."""
    if is_ongoing_end_marker(a) or is_ongoing_end_marker(b):
        return True
    return dates_compatible(a, b)


def _employment_date_confirm_parseable(value: Any) -> bool:
    """Match Confirm parse rules (dots / Korean partial dates), not diff-only parser."""
    try:
        parse_partial_date(value)
        return True
    except ValueError:
        return False


def _employment_start_after_end(start: Any, end: Any) -> bool:
    """True when both sides parse and start bound is after end bound."""
    if is_ongoing_end_marker(end):
        return False
    try:
        start_d = normalize_confirmed_date(start, bound="start")
        end_d = normalize_confirmed_date(end, bound="end")
    except ValueError:
        return False
    if start_d is None or end_d is None:
        return False
    return end_d < start_d


def employment_candidate_dates_need_review(cand: Any) -> bool:
    """True when employment dates are present but not confirm-safe.

    Ongoing end markers (``현재`` / ``Present``) are allowed (confirm → null).
    Other unparseable date tokens or start>end must not be auto-NEW.
    """
    start = _field(cand, "start_date")
    end = _field(cand, "end_date")
    start_text = _norm_str(start)
    if start_text is not None:
        if is_ongoing_end_marker(start_text):
            return True
        if not _employment_date_confirm_parseable(start_text):
            return True
    end_text = _norm_str(end)
    if end_text is not None and not is_ongoing_end_marker(end_text):
        if not _employment_date_confirm_parseable(end_text):
            return True
    if start_text is not None and end_text is not None:
        if _employment_start_after_end(start_text, end_text):
            return True
    return False


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
        if not _employment_end_dates_compatible(
            _field(cand, "end_date"), row.get("end_date")
        ):
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
    new_requires_review=None,
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
                # Invalid/ambiguous employment dates → REVIEW (not auto-NEW).
                if new_requires_review and new_requires_review(cand):
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
                old = similar[0]
                target_id = _as_uuid(old.get("id"))
                # EDUCATION only: unique similar with no conflicting new facts is
                # not a review item. Sparse candidates (e.g. dates omitted) that
                # only repeat school/degree become SAME; null-fills stay UPDATE.
                # EMPLOYMENT / CERTIFICATION keep root REVIEW for unique similar.
                if entity_type == "EDUCATION":
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
                    if all(change == "UPDATE" for _, change, _, _ in field_changes):
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
                        continue
                specs.append(
                    DiffSpec(
                        entity_type=entity_type,
                        candidate_path=path,
                        existing_target_id=target_id,
                        change_type="REVIEW",
                        old_value=old,
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


def _project_date_key(value: Any) -> str | None:
    """Canonical project date key so YYYY-MM-DD and YYYY.MM.DD match.

    Diff-only normalization: does not change Confirm storage or other entities.
    Unparseable values fall back to the trimmed original string.
    """
    text = _norm_str(value)
    if not text:
        return None
    folded = text.split("T", 1)[0] if "T" in text else text
    folded = folded.replace(".", "-").replace("/", "-")
    parts = parse_date_parts(folded)
    if parts is None:
        return text
    year, month, day = parts
    if month is None:
        return f"{year:04d}"
    if day is None:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


def _project_dates_equal(a: Any, b: Any) -> bool:
    if _eq(a, b):
        return True
    ka = _project_date_key(a)
    kb = _project_date_key(b)
    if ka is None or kb is None:
        return False
    return ka == kb


def _project_date_parts(value: Any) -> tuple[int, int | None, int | None] | None:
    """Parse project dates after folding ``.`` / ``/`` separators (PROJECT-only)."""
    text = _norm_str(value)
    if not text:
        return None
    folded = text.split("T", 1)[0] if "T" in text else text
    folded = folded.replace(".", "-").replace("/", "-")
    return parse_date_parts(folded)


def _project_date_precision_compatible(*, candidate: Any, existing: Any) -> bool:
    """Directional PROJECT-only date compatibility for identity/field diff.

    Rules (no year-only, no fuzzy / ±1 month):
    - Same canonical key (format fold of equal precision) → compatible.
    - candidate ``YYYY.MM`` vs existing ``YYYY-MM-DD``: same year/month → compatible.
    - candidate ``YYYY.MM`` vs existing ``YYYY.MM``: exact year/month → compatible.
    - candidate ``YYYY-MM-DD`` vs existing ``YYYY-MM-DD``: exact day only.
    - candidate day precision vs existing month precision → not compatible
      (never shrink candidate day down to month).
    """
    if _project_dates_equal(candidate, existing):
        return True
    pc = _project_date_parts(candidate)
    pe = _project_date_parts(existing)
    if pc is None or pe is None:
        return False
    yc, mc, dc = pc
    ye, me, de = pe
    # Both sides need at least month precision; year-only is never enough.
    if mc is None or me is None:
        return False
    if yc != ye or mc != me:
        return False
    if dc is None:
        # Candidate month precision: existing month or any day in that month.
        return True
    if de is None:
        # Candidate has a day; existing is month-only — do not shrink.
        return False
    return dc == de


def _project_match_key(row: Any) -> tuple[str | None, str | None, str | None, str | None]:
    if isinstance(row, dict):
        return (
            _norm_str(row.get("project_name")),
            _norm_str(row.get("customer_name")),
            _project_date_key(row.get("start_date")),
            _project_date_key(row.get("end_date")),
        )
    return (
        _norm_str(row.project_name),
        _norm_str(row.customer_name),
        _project_date_key(row.start_date),
        _project_date_key(row.end_date),
    )


def _fold_project_ws(value: str) -> str:
    """Collapse Unicode whitespace for project identity/diff helpers only."""
    return "".join(ch for ch in value if not ch.isspace())


def _project_names_identity_compatible(a: str | None, b: str | None) -> bool:
    """Conservative name check for date-anchored project identity fallback.

    Not a general similarity score. After whitespace folding, names match when
    equal, one is a contiguous substring of the other (with length ratio), or
    the longer differs by exactly one contiguous insertion (e.g. optional
    ``범정부`` token) while still covering most of the shorter name.
    """
    if not a or not b:
        return False
    fa = _fold_project_ws(a)
    fb = _fold_project_ws(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    short, long = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    # Reject loose containment where only a small fragment is shared.
    if len(short) * 10 < len(long) * 8:
        return False
    if short in long:
        return True
    gap = len(long) - len(short)
    if gap <= 0:
        return False
    for i in range(len(short) + 1):
        if long[:i] == short[:i] and long[i + gap :] == short[i:]:
            return True
    return False


def _project_customers_ws_equal(a: Any, b: Any) -> bool:
    """True when customers are equal ignoring Unicode whitespace only."""
    if _eq(a, b):
        return True
    na = _norm_str(a)
    nb = _norm_str(b)
    if na is None or nb is None:
        return False
    return _fold_project_ws(na) == _fold_project_ws(nb)


def _project_customer_identity_compatible(
    cand_customer: str | None, row_customer: str | None
) -> bool:
    if cand_customer is None or row_customer is None:
        return True
    return _fold_project_ws(cand_customer) == _fold_project_ws(row_customer)


def _project_identity_fallback_matches(
    project: Any,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Date-anchored identity fallback when exact natural-key match fails.

    Requires both start and end dates on each side, precision-compatible dates
    (directional month↔day rules), customer compatibility (equal ignoring
    whitespace, or one empty), and conservative name compatibility. Never
    matches on name alone.
    """
    cand_name = _norm_str(_field(project, "project_name"))
    cand_customer = _norm_str(_field(project, "customer_name"))
    cand_start = _field(project, "start_date")
    cand_end = _field(project, "end_date")
    if (
        not cand_name
        or not _project_date_key(cand_start)
        or not _project_date_key(cand_end)
    ):
        return []
    out: list[dict[str, Any]] = []
    for row in existing:
        row_name = _norm_str(row.get("project_name"))
        row_start = row.get("start_date")
        row_end = row.get("end_date")
        if (
            not row_name
            or not _project_date_key(row_start)
            or not _project_date_key(row_end)
        ):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_start, existing=row_start
        ):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_end, existing=row_end
        ):
            continue
        if not _project_customer_identity_compatible(
            cand_customer, _norm_str(row.get("customer_name"))
        ):
            continue
        if not _project_names_identity_compatible(cand_name, row_name):
            continue
        out.append(row)
    return out


def _project_name_date_review_matches(
    project: Any,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exact project_name + precision-compatible dates (customer may differ).

    Used when identity fallback rejects a customer mismatch. Does not expand
    name matching beyond exact ``project_name`` equality.
    """
    cand_name = _norm_str(_field(project, "project_name"))
    cand_start = _field(project, "start_date")
    cand_end = _field(project, "end_date")
    if (
        not cand_name
        or not _project_date_key(cand_start)
        or not _project_date_key(cand_end)
    ):
        return []
    out: list[dict[str, Any]] = []
    for row in existing:
        if _norm_str(row.get("project_name")) != cand_name:
            continue
        row_start = row.get("start_date")
        row_end = row.get("end_date")
        if not _project_date_key(row_start) or not _project_date_key(row_end):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_start, existing=row_start
        ):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_end, existing=row_end
        ):
            continue
        out.append(row)
    return out


def _project_customer_date_review_matches(
    project: Any,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Same customer + precision-compatible dates; name ignored (REVIEW-only).

    Both sides must have a non-empty customer that is equal ignoring whitespace.
    Never used for auto-link / SAME / UPDATE.
    """
    cand_customer = _norm_str(_field(project, "customer_name"))
    cand_start = _field(project, "start_date")
    cand_end = _field(project, "end_date")
    if (
        not cand_customer
        or not _project_date_key(cand_start)
        or not _project_date_key(cand_end)
    ):
        return []
    out: list[dict[str, Any]] = []
    for row in existing:
        if not _project_customers_ws_equal(cand_customer, row.get("customer_name")):
            continue
        row_start = row.get("start_date")
        row_end = row.get("end_date")
        if not _project_date_key(row_start) or not _project_date_key(row_end):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_start, existing=row_start
        ):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_end, existing=row_end
        ):
            continue
        out.append(row)
    return out


def _project_month_ordinal(value: Any) -> int | None:
    """Year*12+month for overlap checks; requires at least month precision."""
    parts = _project_date_parts(value)
    if parts is None:
        return None
    year, month, _day = parts
    if month is None:
        return None
    return year * 12 + month


def _project_periods_overlap(
    *,
    cand_start: Any,
    cand_end: Any,
    row_start: Any,
    row_end: Any,
) -> bool:
    """Inclusive month-precision interval overlap (PROJECT REVIEW helpers)."""
    cs = _project_month_ordinal(cand_start)
    ce = _project_month_ordinal(cand_end)
    rs = _project_month_ordinal(row_start)
    re = _project_month_ordinal(row_end)
    if cs is None or ce is None or rs is None or re is None:
        return False
    if cs > ce or rs > re:
        return False
    return cs <= re and rs <= ce


def _project_customer_overlap_review_matches(
    project: Any,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Same customer + precision-compatible start + overlapping period (REVIEW).

    End dates need not be precision-compatible. Name is ignored. Never used for
    auto-link / SAME / UPDATE — REVIEW only so a human can decide.
    """
    cand_customer = _norm_str(_field(project, "customer_name"))
    cand_start = _field(project, "start_date")
    cand_end = _field(project, "end_date")
    if (
        not cand_customer
        or _project_month_ordinal(cand_start) is None
        or _project_month_ordinal(cand_end) is None
    ):
        return []
    out: list[dict[str, Any]] = []
    for row in existing:
        row_customer = row.get("customer_name")
        if not _norm_str(row_customer):
            continue
        if not _project_customers_ws_equal(cand_customer, row_customer):
            continue
        row_start = row.get("start_date")
        row_end = row.get("end_date")
        if (
            _project_month_ordinal(row_start) is None
            or _project_month_ordinal(row_end) is None
        ):
            continue
        if not _project_date_precision_compatible(
            candidate=cand_start, existing=row_start
        ):
            continue
        if not _project_periods_overlap(
            cand_start=cand_start,
            cand_end=cand_end,
            row_start=row_start,
            row_end=row_end,
        ):
            continue
        out.append(row)
    return out


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
            # Exact key missed. Prefer precision-compatible identity fallback
            # before same-name/date-different REVIEW so month↔day same-month
            # pairs (e.g. 2018.06 vs 2018-06-11) link instead of false REVIEW.
            identity = _project_identity_fallback_matches(project, existing)
            if len(identity) > 1:
                specs.append(
                    DiffSpec(
                        entity_type="PROJECT",
                        candidate_path=path,
                        change_type="REVIEW",
                        old_value=identity,
                        new_value=dump,
                        confidence=project.confidence,
                        source_refs=refs,
                    )
                )
                continue
            if len(identity) == 1:
                matches = identity
            else:
                # Same name (+ optional customer) but incompatible dates → REVIEW.
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
                    # Unique similar project → surface existing id for review UX.
                    # Multiple similar candidates stay target-less (ambiguous).
                    target_id = (
                        _as_uuid(similar[0].get("id")) if len(similar) == 1 else None
                    )
                    specs.append(
                        DiffSpec(
                            entity_type="PROJECT",
                            candidate_path=path,
                            existing_target_id=target_id,
                            change_type="REVIEW",
                            old_value=similar if len(similar) > 1 else similar[0],
                            new_value=dump,
                            confidence=project.confidence,
                            source_refs=refs,
                        )
                    )
                    continue
                # Same exact name + dates but incompatible customer (e.g. different
                # non-empty orgs): do not auto-link; unique hit → targeted REVIEW.
                name_date = _project_name_date_review_matches(project, existing)
                if name_date:
                    target_id = (
                        _as_uuid(name_date[0].get("id"))
                        if len(name_date) == 1
                        else None
                    )
                    specs.append(
                        DiffSpec(
                            entity_type="PROJECT",
                            candidate_path=path,
                            existing_target_id=target_id,
                            change_type="REVIEW",
                            old_value=(
                                name_date if len(name_date) > 1 else name_date[0]
                            ),
                            new_value=dump,
                            confidence=project.confidence,
                            source_refs=refs,
                        )
                    )
                    continue
                # Same customer + precision-compatible dates, names not
                # identity-compatible: potential match → REVIEW only (never
                # auto-link / SAME / UPDATE).
                customer_date = _project_customer_date_review_matches(
                    project, existing
                )
                if customer_date:
                    target_id = (
                        _as_uuid(customer_date[0].get("id"))
                        if len(customer_date) == 1
                        else None
                    )
                    specs.append(
                        DiffSpec(
                            entity_type="PROJECT",
                            candidate_path=path,
                            existing_target_id=target_id,
                            change_type="REVIEW",
                            old_value=(
                                customer_date
                                if len(customer_date) > 1
                                else customer_date[0]
                            ),
                            new_value=dump,
                            confidence=project.confidence,
                            source_refs=refs,
                        )
                    )
                    continue
                # Same customer + same start month + overlapping period, but
                # end months differ: REVIEW-only (never auto-link).
                customer_overlap = _project_customer_overlap_review_matches(
                    project, existing
                )
                if customer_overlap:
                    target_id = (
                        _as_uuid(customer_overlap[0].get("id"))
                        if len(customer_overlap) == 1
                        else None
                    )
                    specs.append(
                        DiffSpec(
                            entity_type="PROJECT",
                            candidate_path=path,
                            existing_target_id=target_id,
                            change_type="REVIEW",
                            old_value=(
                                customer_overlap
                                if len(customer_overlap) > 1
                                else customer_overlap[0]
                            ),
                            new_value=dump,
                            confidence=project.confidence,
                            source_refs=refs,
                        )
                    )
                    continue
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
                        additions_only_unmapped=True,
                    )
                )
                continue

        old = matches[0]
        target_id = _as_uuid(old.get("id"))
        field_changes = [
            (field_name, change, old_v, new_v)
            for field_name, change, old_v, new_v in _compare_fields(
                old, dump, PROJECT_COMPARE_FIELDS
            )
            if not (
                (
                    field_name in {"start_date", "end_date"}
                    and _project_date_precision_compatible(
                        candidate=new_v, existing=old_v
                    )
                )
                or (
                    field_name == "customer_name"
                    and _project_customers_ws_equal(old_v, new_v)
                )
                or (
                    # Identity-compatible spelling variants already accepted for
                    # match must not surface as project_name CONFLICT.
                    field_name == "project_name"
                    and _project_names_identity_compatible(
                        _norm_str(old_v), _norm_str(new_v)
                    )
                )
            )
        ]
        relation_specs = _diff_project_relations(
            path=path,
            old_project=old,
            new_project=dump,
            existing_target_id=target_id,
            confidence=project.confidence,
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
    additions_only_unmapped: bool = False,
) -> list[DiffSpec]:
    """Additive Project relation Diffs — never emit removal for omitted codes.

    Child Diff source_refs come from the relation item only — never fall back to
    project.root source_refs.
    """
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
            item_refs = _refs_dump(item.get("source_refs"))

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
                            source_refs=item_refs,
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
                    source_refs=item_refs,
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
