"""Normalize / validate LLM candidate JSON against CodeMaster."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.ai.schemas.profile_candidate import (
    SCHEMA_VERSION,
    CertificationCandidate,
    EducationCandidate,
    EmploymentCandidate,
    ExpertiseCandidate,
    JobCandidate,
    ProfileCandidateDocument,
    ProjectCandidate,
    SkillCandidate,
    SourceRef,
)
from app.core.config import Settings, get_settings

SENSITIVE_KEYS = frozenset(
    {
        "ssn",
        "social_security_number",
        "resident_registration_number",
        "resident_id",
        "certificate_no",
        "certificate_number",
        "account_number",
        "bank_account",
        "passport_no",
        "passport_number",
        "id_card_no",
        "national_id",
        "family_info",
        "detailed_address",
        "full_address",
    }
)


def _strip_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            key: _strip_sensitive(value)
            for key, value in obj.items()
            if key.lower() not in SENSITIVE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_sensitive(item) for item in obj]
    return obj


def _code_lookup(
    catalog: dict[str, tuple[str, bool]],
    code: str | None,
    *,
    expected_type: str,
) -> str | None:
    if not code:
        return None
    text = str(code).strip()
    if not text:
        return None
    entry = catalog.get(text)
    if entry is None:
        return None
    code_type, is_active = entry
    if not is_active or code_type != expected_type:
        return None
    return text


def _normalize_source_refs(
    refs: list[SourceRef] | list[dict[str, Any]] | None,
    *,
    allowed_documents: dict[str, set[int]],
    max_quote: int,
    max_refs: int,
) -> list[SourceRef]:
    if not refs:
        return []
    out: list[SourceRef] = []
    for raw in refs:
        if len(out) >= max_refs:
            break
        if isinstance(raw, SourceRef):
            ref = raw
        elif isinstance(raw, dict):
            ref = SourceRef.model_validate(raw)
        else:
            continue
        doc_id = str(ref.document_id).strip() if ref.document_id else None
        page_no = ref.page_no
        if doc_id is None or doc_id not in allowed_documents:
            doc_id = None
            page_no = None
        elif page_no is not None:
            pages = allowed_documents[doc_id]
            if pages and page_no not in pages:
                page_no = None
        quote = ref.quote_text
        if quote is not None:
            quote = quote.strip()
            if len(quote) > max_quote:
                quote = quote[:max_quote]
            if not quote:
                quote = None
        out.append(
            SourceRef(document_id=doc_id, page_no=page_no, quote_text=quote)
        )
    return out


def normalize_candidate(
    raw: dict[str, Any],
    *,
    catalog: dict[str, tuple[str, bool]],
    allowed_documents: dict[str, set[int]],
    settings: Settings | None = None,
) -> ProfileCandidateDocument:
    """Validate codes, source_refs, and strip sensitive keys.

    ``catalog`` maps code → (code_type, is_active).
    ``allowed_documents`` maps document_id str → set of valid page numbers
    (empty set means any page is accepted for that document).
    """
    cfg = settings or get_settings()
    max_quote = int(cfg.analysis_max_quote_chars)
    max_refs = int(cfg.analysis_max_source_refs)

    cleaned = _strip_sensitive(raw if isinstance(raw, dict) else {})
    if not isinstance(cleaned, dict):
        cleaned = {}

    doc = ProfileCandidateDocument.model_validate(cleaned)
    doc.schema_version = SCHEMA_VERSION

    jobs: list[JobCandidate] = []
    for job in doc.jobs:
        code = _code_lookup(catalog, job.code, expected_type="JOB")
        jobs.append(
            job.model_copy(
                update={
                    "code": code,
                    "source_refs": _normalize_source_refs(
                        job.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    ),
                }
            )
        )
    doc.jobs = jobs

    skills: list[SkillCandidate] = []
    for skill in doc.skills:
        # TECH only — reject EXP (and any other) codes in skills.
        code = _code_lookup(catalog, skill.code, expected_type="TECH")
        skills.append(
            skill.model_copy(
                update={
                    "code": code,
                    "source_refs": _normalize_source_refs(
                        skill.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    ),
                }
            )
        )
    doc.skills = skills

    expertise: list[ExpertiseCandidate] = []
    for exp in doc.expertise:
        code = _code_lookup(catalog, exp.code, expected_type="EXP")
        expertise.append(
            exp.model_copy(
                update={
                    "code": code,
                    "source_refs": _normalize_source_refs(
                        exp.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    ),
                }
            )
        )
    doc.expertise = expertise

    employment: list[EmploymentCandidate] = []
    for row in doc.employment_history:
        employment.append(
            row.model_copy(
                update={
                    "source_refs": _normalize_source_refs(
                        row.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    )
                }
            )
        )
    doc.employment_history = employment

    education: list[EducationCandidate] = []
    for row in doc.education:
        education.append(
            row.model_copy(
                update={
                    "source_refs": _normalize_source_refs(
                        row.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    )
                }
            )
        )
    doc.education = education

    certifications: list[CertificationCandidate] = []
    for row in doc.certifications:
        certifications.append(
            row.model_copy(
                update={
                    "source_refs": _normalize_source_refs(
                        row.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    )
                }
            )
        )
    doc.certifications = certifications

    projects: list[ProjectCandidate] = []
    for project in doc.projects:
        jobs_rel = []
        for ref in project.jobs:
            jobs_rel.append(
                ref.model_copy(
                    update={"code": _code_lookup(catalog, ref.code, expected_type="JOB")}
                )
            )
        skills_rel = []
        for ref in project.skills:
            skills_rel.append(
                ref.model_copy(
                    update={
                        "code": _code_lookup(catalog, ref.code, expected_type="TECH")
                    }
                )
            )
        exp_rel = []
        for ref in project.expertise:
            exp_rel.append(
                ref.model_copy(
                    update={"code": _code_lookup(catalog, ref.code, expected_type="EXP")}
                )
            )
        biz_rel = []
        for ref in project.business_domains:
            biz_rel.append(
                ref.model_copy(
                    update={"code": _code_lookup(catalog, ref.code, expected_type="BIZ")}
                )
            )
        cust_rel = []
        for ref in project.customer_types:
            cust_rel.append(
                ref.model_copy(
                    update={
                        "code": _code_lookup(
                            catalog, ref.code, expected_type="CUSTOMER_TYPE"
                        )
                    }
                )
            )
        projects.append(
            project.model_copy(
                update={
                    "jobs": jobs_rel,
                    "skills": skills_rel,
                    "expertise": exp_rel,
                    "business_domains": biz_rel,
                    "customer_types": cust_rel,
                    "source_refs": _normalize_source_refs(
                        project.source_refs,
                        allowed_documents=allowed_documents,
                        max_quote=max_quote,
                        max_refs=max_refs,
                    ),
                }
            )
        )
    doc.projects = projects

    # Drop accidental sensitive keys that may have nested under profile dump.
    storage = _strip_sensitive(doc.to_storage_dict())
    return ProfileCandidateDocument.model_validate(storage)


def document_page_ranges(
    documents: list[tuple[UUID, list[int]]],
) -> dict[str, set[int]]:
    """Build allowed document_id → page_no set map for source_ref validation."""
    out: dict[str, set[int]] = {}
    for doc_id, pages in documents:
        out[str(doc_id)] = set(pages)
    return out
