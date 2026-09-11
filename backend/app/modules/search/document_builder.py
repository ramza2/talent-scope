"""Deterministic Confirmed → Search Document builder (no LLM/Embedding/MinIO)."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

from app.modules.search.schemas import (
    GRADE_LABELS,
    OBJECT_TYPE_PROFILE,
    OBJECT_TYPE_PROJECT,
    PROFILE_SEARCH_TEXT_MAX_CHARS,
    PROJECT_SEARCH_TEXT_MAX_CHARS,
    SEARCH_DOCUMENT_VERSION,
    SECTION_VALUE_MAX_CHARS,
    SOURCE_WEIGHT_DEFAULT,
    SearchDocument,
)

_BLANK_LINES_RE = re.compile(r"\n{3,}")


def content_hash(search_text: str) -> str:
    return hashlib.sha256(search_text.encode("utf-8")).hexdigest()


def grade_display(grade: str | None) -> str | None:
    if not grade:
        return None
    label = GRADE_LABELS.get(grade)
    if label:
        return f"{grade} / {label}"
    return grade


def normalize_search_text(text: str, *, max_chars: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    joined = "\n".join(lines)
    joined = _BLANK_LINES_RE.sub("\n\n", joined).strip()
    if len(joined) > max_chars:
        joined = joined[:max_chars].rstrip()
    return joined


def _cap(value: Any, *, max_chars: int = SECTION_VALUE_MAX_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _codes(rows: list[dict[str, Any]] | None, code_key: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        code = row.get(code_key) or row.get("code")
        if not code:
            continue
        text = str(code).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _names(rows: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for row in rows or []:
        name = _cap(row.get("name") or row.get("raw_value") or row.get("code"))
        if name:
            out.append(name)
    return out


def _section(title: str, lines: list[str]) -> list[str]:
    body = [line for line in lines if line]
    if not body:
        return []
    return [f"[{title}]"] + body + [""]


def build_profile_search_document(
    *,
    person_id: UUID,
    person_status: str,
    snapshot: dict[str, Any],
) -> SearchDocument:
    profile = snapshot.get("profile") or {}
    lines: list[str] = []

    profile_lines: list[str] = []
    name = _cap(profile.get("name"))
    if name:
        profile_lines.append(f"이름: {name}")
    company = _cap(profile.get("affiliation_company"))
    if company:
        profile_lines.append(f"소속: {company}")
    department = _cap(profile.get("department"))
    if department:
        profile_lines.append(f"부서: {department}")
    title = _cap(profile.get("current_title"))
    if title:
        profile_lines.append(f"직책: {title}")
    employment_type = _cap(profile.get("employment_type"))
    if employment_type:
        profile_lines.append(f"고용형태: {employment_type}")
    grade = grade_display(profile.get("technical_grade"))
    if grade:
        profile_lines.append(f"기술등급: {grade}")

    confirmed = profile.get("career_confirmed_months")
    calculated = profile.get("career_calculated_months")
    career_months = confirmed if confirmed is not None else calculated
    if career_months is not None:
        profile_lines.append(f"총 경력: {career_months}개월")
    summary = _cap(profile.get("profile_summary"))
    if summary:
        profile_lines.append(f"프로필 요약: {summary}")
    lines.extend(_section("프로필", profile_lines))

    lines.extend(_section("직무", _names(snapshot.get("jobs"))))
    lines.extend(_section("기술", _names(snapshot.get("skills"))))
    lines.extend(_section("전문분야", _names(snapshot.get("expertise"))))

    emp_lines: list[str] = []
    for emp in snapshot.get("employment_history") or []:
        parts = [_cap(emp.get("company_name")), _cap(emp.get("title"))]
        period = " ~ ".join(
            p for p in (_cap(emp.get("start_date")), _cap(emp.get("end_date"))) if p
        )
        head = " | ".join(p for p in parts if p)
        if period:
            head = f"{head} | {period}" if head else period
        if head:
            emp_lines.append(head)
        resp = _cap(emp.get("responsibilities"))
        if resp:
            emp_lines.append(resp)
    lines.extend(_section("근무경력", emp_lines))

    edu_lines: list[str] = []
    for edu in snapshot.get("education") or []:
        line = " | ".join(
            p
            for p in (
                _cap(edu.get("school_name")),
                _cap(edu.get("major")),
                _cap(edu.get("degree")),
            )
            if p
        )
        if line:
            edu_lines.append(line)
    lines.extend(_section("학력", edu_lines))

    cert_lines: list[str] = []
    for cert in snapshot.get("certifications") or []:
        # certificate_no is intentionally omitted (sensitive).
        line = " | ".join(
            p
            for p in (_cap(cert.get("certification_name")), _cap(cert.get("issuer")))
            if p
        )
        if line:
            cert_lines.append(line)
    lines.extend(_section("자격", cert_lines))

    search_text = normalize_search_text(
        "\n".join(lines), max_chars=PROFILE_SEARCH_TEXT_MAX_CHARS
    )
    metadata: dict[str, Any] = {
        "search_document_version": SEARCH_DOCUMENT_VERSION,
        "content_hash": content_hash(search_text),
        "profile_version": profile.get("profile_version"),
        "person_status": person_status,
        "technical_grade": profile.get("technical_grade"),
        "career_months": career_months,
        "career_confirmed_months": confirmed,
        "career_calculated_months": calculated,
        "job_codes": _codes(snapshot.get("jobs"), "job_code"),
        "skill_codes": _codes(snapshot.get("skills"), "tech_code"),
        "expertise_codes": _codes(snapshot.get("expertise"), "exp_code"),
    }
    if company:
        metadata["affiliation_company"] = company
    if title:
        metadata["current_title"] = title

    return SearchDocument(
        person_id=person_id,
        object_type=OBJECT_TYPE_PROFILE,
        object_id=person_id,
        search_text=search_text,
        metadata=metadata,
        source_weight=SOURCE_WEIGHT_DEFAULT,
    )


def build_project_search_document(
    *,
    person_id: UUID,
    profile_version: int | None,
    project: dict[str, Any],
) -> SearchDocument | None:
    project_id_raw = project.get("id")
    if not project_id_raw:
        return None
    try:
        project_id = UUID(str(project_id_raw))
    except (TypeError, ValueError):
        return None

    lines: list[str] = []
    proj_lines: list[str] = []
    name = _cap(project.get("project_name"))
    if name:
        proj_lines.append(f"프로젝트명: {name}")
    customer = _cap(project.get("customer_name"))
    if customer:
        proj_lines.append(f"고객사: {customer}")
    period = " ~ ".join(
        p for p in (_cap(project.get("start_date")), _cap(project.get("end_date"))) if p
    )
    if period:
        proj_lines.append(f"기간: {period}")
    duration = project.get("duration_months")
    if duration is not None:
        proj_lines.append(f"수행기간: {duration}개월")
    lines.extend(_section("프로젝트", proj_lines))

    lines.extend(_section("역할", _names(project.get("jobs"))))
    lines.extend(_section("기술", _names(project.get("skills"))))
    lines.extend(_section("전문분야", _names(project.get("expertise"))))
    lines.extend(_section("사업분야", _names(project.get("business_domains"))))
    lines.extend(_section("고객유형", _names(project.get("customer_types"))))

    resp = _cap(project.get("responsibilities"))
    if resp:
        lines.extend(_section("수행내용", [resp]))
    summary = _cap(project.get("project_summary"))
    if summary:
        lines.extend(_section("프로젝트 요약", [summary]))

    search_text = normalize_search_text(
        "\n".join(lines), max_chars=PROJECT_SEARCH_TEXT_MAX_CHARS
    )

    expertise_meta: list[dict[str, Any]] = []
    for row in project.get("expertise") or []:
        code = row.get("code") or row.get("exp_code")
        if not code:
            continue
        item: dict[str, Any] = {"code": str(code).strip()}
        if row.get("evidence_type"):
            item["evidence_type"] = row.get("evidence_type")
        expertise_meta.append(item)

    metadata: dict[str, Any] = {
        "search_document_version": SEARCH_DOCUMENT_VERSION,
        "content_hash": content_hash(search_text),
        "profile_version": profile_version,
        "project_id": str(project_id),
        "start_date": project.get("start_date"),
        "end_date": project.get("end_date"),
        "duration_months": duration,
        "source_type": project.get("source_type"),
        "job_codes": _codes(project.get("jobs"), "code"),
        "skill_codes": _codes(project.get("skills"), "code"),
        "expertise_codes": _codes(project.get("expertise"), "code"),
        "business_domain_codes": _codes(project.get("business_domains"), "code"),
        "customer_type_codes": _codes(project.get("customer_types"), "code"),
        "expertise": expertise_meta,
    }

    return SearchDocument(
        person_id=person_id,
        object_type=OBJECT_TYPE_PROJECT,
        object_id=project_id,
        search_text=search_text,
        metadata=metadata,
        source_weight=SOURCE_WEIGHT_DEFAULT,
    )


def build_search_documents_for_person(
    *,
    person_id: UUID,
    person_status: str,
    snapshot: dict[str, Any],
) -> list[SearchDocument]:
    profile = snapshot.get("profile") or {}
    profile_version = profile.get("profile_version")
    docs: list[SearchDocument] = [
        build_profile_search_document(
            person_id=person_id,
            person_status=person_status,
            snapshot=snapshot,
        )
    ]
    for project in snapshot.get("projects") or []:
        doc = build_project_search_document(
            person_id=person_id,
            profile_version=profile_version,
            project=project,
        )
        if doc is not None:
            docs.append(doc)
    return docs
