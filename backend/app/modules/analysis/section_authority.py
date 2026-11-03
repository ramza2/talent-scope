"""Conservative v25 section-authority guard for compact candidates.

The LLM can confuse project customers, employers and qualifications. Exact
page-text matches prove that a literal exists, not that it belongs to a field.
This guard only removes high-confidence cross-section leakage; unstructured
or ambiguous sources are intentionally left for human review.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.ai.schemas.profile_candidate import ProfileCandidateDocument

_EMPLOYER_HEADER = re.compile(
    r"(?:회사명\s*기간\s*(?:직위|직급)\s*담당업무"
    r"|직장명\s*근무기간|근무처(?:명)?\s*근무기간)",
    re.IGNORECASE,
)
_EMPLOYER_END = re.compile(
    r"교육명|프로젝트명|주요\s*수행\s*프로젝트|경력기술서|"
    r"수행\s*프로젝트|기술\s*경력",
    re.IGNORECASE,
)
_EDUCATION_HEADER = re.compile(r"학력(?:사항)?|학교명", re.IGNORECASE)
_CERTIFICATION_HEADER = re.compile(
    r"자격증명|자격증|자격\s*취득|취득\s*자격", re.IGNORECASE
)
_PROJECT_SIGNAL = re.compile(r"프로젝트|발주처|고객사|수행사", re.IGNORECASE)


def _key(value: str | None) -> str:
    return re.sub(r"[\W_]+", "", (value or "").casefold())


def _month(value: str | None) -> str:
    match = re.search(r"(\d{4})[.\-/](\d{1,2})", value or "")
    if not match:
        return ""
    return f"{match.group(1)}.{int(match.group(2)):02d}"


def _employer_regions(page: str) -> list[str]:
    regions: list[str] = []
    for header in _EMPLOYER_HEADER.finditer(page):
        tail = page[header.end() :]
        end = _EMPLOYER_END.search(tail)
        # A whole page without a section boundary is too ambiguous.
        if end is None:
            continue
        regions.append(tail[: end.start()])
    return regions


def _near_header(page: str, value: str, header: re.Pattern[str]) -> bool:
    target = _key(value)
    if not target:
        return False
    for match in header.finditer(page):
        # Table content can be wrapped or interleaved with another column.
        if target in _key(page[match.end() : match.end() + 650]):
            return True
    return False


def _project_page(page: str) -> bool:
    return len(_PROJECT_SIGNAL.findall(page)) >= 2


def guard_compact_section_authority(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: Mapping[tuple[str, int], str] | None,
    allowed_documents: Mapping[str, set[int]],
) -> tuple[ProfileCandidateDocument, dict[str, int]]:
    """Remove only cross-section rows supported by recognizable source tables.

    No source/table authority => fail open. Never changes confirmed profiles,
    source pages, relations, or candidate rows that remain in the result.
    """
    report = {
        "employment_dropped": 0,
        "education_dropped": 0,
        "certifications_dropped": 0,
        "projects_dropped": 0,
    }
    if not page_texts:
        return candidate, report

    pages = {
        (str(doc_id), page_no): value
        for (doc_id, page_no), value in page_texts.items()
        if str(doc_id) in allowed_documents
        and page_no in allowed_documents[str(doc_id)]
        and isinstance(value, str)
        and value.strip()
    }
    if not pages:
        return candidate, report

    employer_regions = [
        region for page in pages.values() for region in _employer_regions(page)
    ]
    has_education_authority = any(
        _EDUCATION_HEADER.search(page) for page in pages.values()
    )
    has_certification_authority = any(
        _CERTIFICATION_HEADER.search(page) for page in pages.values()
    )
    project_pages = [page for page in pages.values() if _project_page(page)]

    def in_employer_row(name: str | None) -> bool:
        return bool(_key(name)) and any(
            _key(name) in _key(region) for region in employer_regions
        )

    def in_project_page(name: str | None) -> bool:
        return bool(_key(name)) and any(
            _key(name) in _key(page) for page in project_pages
        )

    employment = []
    for item in candidate.employment_history:
        # Missing start date, employer-table authority elsewhere, and a
        # customer mention in project rows: clear customer-as-employer leak.
        if (
            item.company_name
            and not item.start_date
            and employer_regions
            and not in_employer_row(item.company_name)
            and in_project_page(item.company_name)
        ):
            report["employment_dropped"] += 1
            continue
        employment.append(item)

    education = []
    for item in candidate.education:
        name = item.school_name or ""
        in_education = any(
            _near_header(page, name, _EDUCATION_HEADER)
            for page in pages.values()
        )
        literal_present = any(_key(name) in _key(page) for page in pages.values())
        # Unsupported/hallucinated school names: only prune when a
        # recognizable school table exists and no item-specific provenance.
        if (
            name
            and has_education_authority
            and not in_education
            and not item.source_refs
            and (not literal_present or in_project_page(name))
        ):
            report["education_dropped"] += 1
            continue
        education.append(item)

    certifications = []
    for item in candidate.certifications:
        name = item.certification_name or ""
        in_certification = any(
            _near_header(page, name, _CERTIFICATION_HEADER)
            for page in pages.values()
        )
        # A technical term occurring only in project history is not evidence
        # of a qualification. No recognizable cert table => fail open.
        if (
            name
            and has_certification_authority
            and not in_certification
            and in_project_page(name)
        ):
            report["certifications_dropped"] += 1
            continue
        certifications.append(item)

    projects = []
    for project in candidate.projects:
        name = project.project_name or ""
        customer = project.customer_name or ""
        duplicate_employer_row = False
        if name and customer and _key(name) == _key(customer):
            for job in employment:
                if (
                    _key(job.company_name) != _key(name)
                    or not _month(job.start_date)
                    or _month(job.start_date) != _month(project.start_date)
                ):
                    continue
                # The sole project quote points at an employer row, not
                # at an independently named project/engagement row.
                if project.source_refs and all(
                    _key(ref.quote_text) and any(
                        _key(ref.quote_text) in _key(region)
                        and _key(name) in _key(region)
                        for region in _employer_regions(
                            pages.get(
                                (str(ref.document_id), ref.page_no), ""
                            )
                        )
                    )
                    for ref in project.source_refs
                ):
                    duplicate_employer_row = True
                if duplicate_employer_row:
                    break
        if duplicate_employer_row:
            report["projects_dropped"] += 1
            continue
        projects.append(project)

    if not any(report.values()):
        return candidate, report
    return candidate.model_copy(
        update={
            "employment_history": employment,
            "education": education,
            "certifications": certifications,
            "projects": projects,
        }
    ), report
