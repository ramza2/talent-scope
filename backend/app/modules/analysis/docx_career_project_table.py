"""DOCX project-table structuring and candidate recall (경력기술서/이력서).

DOCX → PDF extraction + DOC-CAREER / DOC-RESUME analysis only when a verified
사업명/기간/(역할|담당업무|발주처) table is present. Does not alter
PDF/HWP/PPTX/KOSA native paths. Never mutates Confirmed Profile tables.
"""

from __future__ import annotations

import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Mapping, Sequence

from app.ai.schemas.profile_candidate import (
    ProfileCandidateDocument,
    ProjectCandidate,
    SourceRef,
)

CAREER_TABLE_MARKER = "[CAREER_PROJECT_TABLE]"
_LAYOUT_KEY = "docx_career_project_table"
_DOCX_PROJECT_TABLE_TYPES = frozenset({"DOC-CAREER", "DOC-RESUME"})

_NON_WORD_RE = re.compile(r"[^0-9a-z가-힣]+")
_PERIOD_FULL_RE = re.compile(
    r"^\s*((?:19|20)\d{2})\s*[./-]\s*(\d{1,2})"
    r"(?:\s*[./-]\s*(\d{1,2}))?"
    r"\s*[~～\-–—]\s*"
    r"((?:19|20)\d{2})\s*[./-]\s*(\d{1,2})"
    r"(?:\s*[./-]\s*(\d{1,2}))?\s*$"
)
_PERIOD_SAME_YEAR_RE = re.compile(
    r"^\s*((?:19|20)\d{2})\s*[./-]\s*(\d{1,2})"
    r"(?:\s*[./-]\s*(\d{1,2}))?"
    r"\s*[~～\-–—]\s*"
    r"(\d{1,2})(?:\s*[./-]\s*(\d{1,2}))?\s*$"
)


def _flat(value: object) -> str:
    return " ".join(str(value or "").split())


def _label(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _flat(value)).lower()


def _match_key(value: str | None) -> str:
    return _NON_WORD_RE.sub("", unicodedata.normalize("NFKC", value or "").lower())


def career_project_header_columns(cells: Sequence[object]) -> dict[str, int]:
    """Map 사업명/기간/역할/담당업무 상세/발주처 headers. Fail closed otherwise.

    Requires project_name + period and at least one of role/duty/customer so
    ordinary resume tables (회사명·학력 등) never match by accident.
    """
    found: dict[str, int] = {}
    for index, cell in enumerate(cells):
        key = _label(cell)
        if not key:
            continue
        if any(token in key for token in ("사업명", "프로젝트명", "프로젝트수행명")):
            found.setdefault("project_name", index)
        elif key == "기간" or any(
            token in key for token in ("참여기간", "수행기간", "프로젝트기간")
        ):
            found.setdefault("period", index)
        elif key == "역할" or any(
            token in key for token in ("담당역할", "수행역할")
        ):
            found.setdefault("role", index)
        elif "담당업무" in key:
            found.setdefault("duty", index)
        elif any(token in key for token in ("발주처", "고객사", "발주기관")):
            found.setdefault("customer", index)
    if not {"project_name", "period"} <= found.keys():
        return {}
    if not ({"role", "duty", "customer"} & found.keys()):
        return {}
    return found


def parse_career_period(period: str) -> tuple[str, str] | None:
    """Normalize career-table periods including same-year short ends (2022.04~12)."""
    text = _flat(period).replace(" ", "")
    if not text:
        return None
    full = _PERIOD_FULL_RE.fullmatch(text)
    if full is not None:
        sy, sm, _sd, ey, em, _ed = full.groups()
        return f"{int(sy):04d}.{int(sm):02d}", f"{int(ey):04d}.{int(em):02d}"
    short = _PERIOD_SAME_YEAR_RE.fullmatch(text)
    if short is not None:
        sy, sm, _sd, em, _ed = short.groups()
        year = int(sy)
        start_m, end_m = int(sm), int(em)
        if not (1 <= start_m <= 12 and 1 <= end_m <= 12):
            return None
        if end_m < start_m:
            return None
        return f"{year:04d}.{start_m:02d}", f"{year:04d}.{end_m:02d}"
    return None


def _row_dict(
    *,
    page_no: int,
    row_index: int,
    cells: list[str],
    columns: dict[str, int],
) -> dict[str, Any] | None:
    def _at(field: str) -> str:
        idx = columns.get(field)
        if idx is None or idx >= len(cells):
            return ""
        return cells[idx]

    title = _at("project_name")
    period_raw = _at("period")
    dates = parse_career_period(period_raw)
    if dates is None or len(title) < 4:
        return None
    start, end = dates
    # quote_text is resolved later against original DocumentPage text —
    # never persist synthetic pipe-joined rows as Evidence quotes.
    return {
        "page_no": page_no,
        "row_index": row_index,
        "project_name": title,
        "period_raw": period_raw,
        "start_date": start,
        "end_date": end,
        "role": _at("role") or None,
        "duty": _at("duty") or None,
        "customer_name": _at("customer") or None,
        "cells": cells,
    }


def extract_docx_career_project_rows_from_page(
    page: object, *, page_no: int
) -> list[dict[str, Any]]:
    """Read 사업명/기간… tables via PyMuPDF ``find_tables(strategy='lines')``."""
    try:
        finder = getattr(page, "find_tables", None)
        if not callable(finder):
            return []
        try:
            tables = finder(strategy="lines").tables
        except TypeError:
            # Older pymupdf without strategy kw — fail closed for this path.
            return []
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for table in tables or []:
        try:
            data = table.extract() or []
        except Exception:
            continue
        if not data or len(data) > 200:
            continue
        start = None
        columns: dict[str, int] = {}
        for i, cells in enumerate(data[:8]):
            if not isinstance(cells, (list, tuple)):
                continue
            columns = career_project_header_columns(
                [_flat(cell) for cell in cells]
            )
            if columns:
                start = i + 1
                break
        if start is None:
            continue
        for index in range(start, len(data)):
            raw = data[index]
            if not isinstance(raw, (list, tuple)):
                continue
            cells = [_flat(cell) for cell in raw]
            if len(cells) <= max(columns.values()):
                continue
            # Skip empty continuation shells left by extractor noise.
            if not any(cells):
                continue
            parsed = _row_dict(
                page_no=page_no, row_index=index, cells=cells, columns=columns
            )
            if parsed is not None:
                rows.append(parsed)
    return rows


def extract_docx_career_project_rows_from_docx(
    docx_bytes: bytes,
) -> list[dict[str, Any]]:
    """Read real DOCX OOXML table rows when PDF find_tables loses the header.

    Only a verified project header + dated rows are accepted. Bounded document
    XML decompression avoids oversized/untrusted input; no external entities.
    """
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 16 * 1024 * 1024:
                return []
            root = ET.fromstring(archive.read(info))
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, OSError):
        return []

    result: list[dict[str, Any]] = []
    for table in root.findall(".//w:tbl", ns)[:500]:
        table_rows = table.findall("./w:tr", ns)
        if len(table_rows) > 200:
            continue
        header: dict[str, int] = {}
        header_index: int | None = None
        extracted: list[list[str]] = []
        for row in table_rows:
            values: list[str] = []
            cells = row.findall("./w:tc", ns)
            if len(cells) > 40:
                break
            for cell in cells:
                segments = []
                for paragraph in cell.findall("./w:p", ns):
                    text = "".join(
                        node.text or "" for node in paragraph.findall(".//w:t", ns)
                    )
                    if text.strip():
                        segments.append(text)
                values.append(_flat(" ".join(segments)))
            extracted.append(values)
        for i, cells in enumerate(extracted[:8]):
            columns = career_project_header_columns(cells)
            if columns:
                header, header_index = columns, i
                break
        if header_index is None:
            continue
        for index in range(header_index + 1, len(extracted)):
            cells = extracted[index]
            if len(cells) <= max(header.values()):
                continue
            row = _row_dict(
                page_no=0, row_index=index, cells=cells, columns=header
            )
            if row is not None:
                result.append(row)
    return result


def extract_docx_career_project_rows_by_page(
    docx_bytes: bytes, pages: Sequence[Any]
) -> dict[int, list[dict[str, Any]]]:
    """Locate native DOCX rows on a unique PDF-extracted page, quote-verified.

    Page assignment is never guessed. Titles are preferred; a literal period
    is allowed only when exactly one page matches.
    """
    rows = extract_docx_career_project_rows_from_docx(docx_bytes)
    by_page: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        title_matches = [
            int(page.page_no)
            for page in pages
            if literal_quote_from_page(
                getattr(page, "extracted_text", None) or "",
                title=row["project_name"],
                period_raw="",
            )
        ]
        matches = title_matches
        if not matches:
            matches = [
                int(page.page_no)
                for page in pages
                if literal_quote_from_page(
                    getattr(page, "extracted_text", None) or "",
                    title="",
                    period_raw=row["period_raw"],
                )
            ]
        if len(matches) != 1:
            continue
        page_no = matches[0]
        by_page.setdefault(page_no, []).append({**row, "page_no": page_no})
    return by_page


def extract_docx_career_project_rows_from_pdf(
    pdf_bytes: bytes,
) -> dict[int, list[dict[str, Any]]]:
    """Page-no → structured career project rows for a DOCX-converted PDF."""
    import pymupdf

    by_page: dict[int, list[dict[str, Any]]] = {}
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for index in range(doc.page_count):
                page_no = index + 1
                rows = extract_docx_career_project_rows_from_page(
                    doc.load_page(index), page_no=page_no
                )
                if rows:
                    by_page[page_no] = rows
    except Exception:
        return {}
    return by_page


def attach_docx_career_tables_to_pages(
    pdf_bytes: bytes, pages: Sequence[Any], *, docx_bytes: bytes | None = None
) -> None:
    """Store verified rows on layout_json; prefer native DOCX if PDF loses rows."""
    by_page = extract_docx_career_project_rows_from_pdf(pdf_bytes)
    if docx_bytes is not None:
        native = extract_docx_career_project_rows_by_page(docx_bytes, pages)
        if sum(map(len, native.values())) > sum(map(len, by_page.values())):
            by_page = native
    if not by_page:
        return
    for page in pages:
        page_no = int(getattr(page, "page_no", 0) or 0)
        rows = by_page.get(page_no)
        if not rows:
            continue
        layout = getattr(page, "layout_json", None)
        if not isinstance(layout, dict):
            layout = {}
        else:
            layout = dict(layout)
        layout[_LAYOUT_KEY] = {
            "header": ["사업명", "기간", "역할", "담당업무 상세", "발주처"],
            "rows": rows,
        }
        page.layout_json = layout


def format_career_table_block(rows: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic structured block appended to LLM page text."""
    if not rows:
        return ""
    lines = [
        CAREER_TABLE_MARKER,
        "| 사업명 | 기간 | 역할 | 담당업무 상세 | 발주처 |",
    ]
    for row in rows:
        lines.append(
            "| {name} | {period} | {role} | {duty} | {customer} |".format(
                name=_flat(row.get("project_name")),
                period=_flat(row.get("period_raw") or ""),
                role=_flat(row.get("role") or ""),
                duty=_flat(row.get("duty") or ""),
                customer=_flat(row.get("customer_name") or ""),
            )
        )
    return "\n".join(lines)


def append_career_table_block(page_text: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """Preserve original page text; append structured rows once."""
    block = format_career_table_block(rows)
    if not block:
        return page_text
    base = page_text or ""
    if CAREER_TABLE_MARKER in base:
        return base
    if not base.strip():
        return block
    return f"{base.rstrip()}\n\n{block}"


def rows_from_layout(layout_json: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(layout_json, dict):
        return []
    payload = layout_json.get(_LAYOUT_KEY)
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("project_name") and row.get("start_date"):
            out.append(dict(row))
    return out


def original_page_body(text: str) -> str:
    """Strip the analysis-only structured block; keep DocumentPage text."""
    body = text or ""
    if CAREER_TABLE_MARKER in body:
        body = body.split(CAREER_TABLE_MARKER, 1)[0]
    return body.rstrip()


def literal_quote_from_page(
    original_text: str, *, title: str, period_raw: str
) -> str | None:
    """Return a contiguous original-page substring suitable for Evidence.

    Never returns synthetic ``|``-joined table rows. Prefers the project title,
    then a whitespace-flexible title match (preserving original newlines),
    then the period token when it appears literally.
    """
    original = original_text or ""
    title = _flat(title)
    period = _flat(period_raw)
    if title and title in original:
        return title[:500]
    if title:
        pattern = re.compile(
            re.escape(title).replace(r"\ ", r"\s+"),
            flags=re.MULTILINE,
        )
        match = pattern.search(original)
        if match is not None:
            return original[match.start() : match.end()][:500]
    if period and period in original:
        return period[:500]
    # Period may appear with spacing artifacts from PDF extract (2022 .04~12).
    if period:
        loose = re.compile(
            re.escape(period).replace(r"\ ", r"\s*").replace(r"\.", r"\s*\.\s*"),
        )
        match = loose.search(original)
        if match is not None:
            return original[match.start() : match.end()][:500]
    return None


def parse_career_table_rows_from_text(
    text: str, *, page_no: int, document_id: str | None = None
) -> list[dict[str, Any]]:
    """Parse the structured LLM block; quotes resolved against original body."""
    if CAREER_TABLE_MARKER not in (text or ""):
        return []
    original = original_page_body(text)
    section = text.split(CAREER_TABLE_MARKER, 1)[1]
    rows: list[dict[str, Any]] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or ("사업명" in line and "기간" in line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        # Allow short rows (name|period) and full 5-col rows.
        while len(cells) < 5:
            cells.append("")
        name, period_raw, role, duty, customer = cells[:5]
        dates = parse_career_period(period_raw)
        title = _flat(name)
        period = _flat(period_raw)
        if dates is None or len(title) < 4:
            continue
        start, end = dates
        quote = literal_quote_from_page(
            original, title=title, period_raw=period
        )
        if quote is None or "|" in quote:
            # No verified contiguous original quote → skip (fail closed).
            continue
        rows.append(
            {
                "document_id": document_id,
                "page_no": page_no,
                "project_name": title,
                "period_raw": period,
                "start_date": start,
                "end_date": end,
                "role": _flat(role) or None,
                "duty": _flat(duty) or None,
                "customer_name": _flat(customer) or None,
                "quote_text": quote,
            }
        )
    return rows


def _title_key(value: str | None) -> str:
    key = _match_key(value)
    for suffix in ("애플리케이션", "프로젝트", "시스템"):
        if key.endswith(suffix) and len(key) - len(suffix) >= 4:
            return key[: -len(suffix)]
    return key


def _month_pair(start: str | None, end: str | None) -> tuple[str, str] | None:
    if not start or not end:
        return None
    return start[:7], end[:7]


def _is_title_compatible(candidate: str | None, verified: str | None) -> bool:
    left, right = _title_key(candidate), _title_key(verified)
    if not left or not right:
        return False
    if left == right:
        return True
    # Truncation / multi-line merge: one contains the other with enough signal.
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 4 and shorter in longer


def _employment_duty_keys(candidate: ProfileCandidateDocument) -> set[str]:
    keys: set[str] = set()
    for row in candidate.employment_history or []:
        for chunk in re.split(r"[,/·\n|;]", row.responsibilities or ""):
            key = _match_key(chunk)
            if 2 <= len(key) <= 40:
                keys.add(key)
    return keys


def _is_proven_employment_mirror_on_page_one(
    project: ProjectCandidate,
    candidate: ProfileCandidateDocument,
    document_ids: set[str],
) -> bool:
    """Exclude a false project only with same DOCX p1 provenance and job period.

    A generic project title or matching employer department alone is NOT enough.
    Genuine projects with page-2 or other-document evidence remain untouched.
    """
    refs = list(project.source_refs or [])
    if not refs or not all(
        str(ref.document_id or "") in document_ids and ref.page_no == 1
        for ref in refs
    ):
        return False
    title = _title_key(project.project_name)
    months = _month_pair(project.start_date, project.end_date)
    if not title or months is None:
        return False
    for employment in candidate.employment_history or []:
        if months != _month_pair(employment.start_date, employment.end_date):
            continue
        for field in (employment.department, employment.responsibilities):
            for part in re.split(r"[,/·\\n|;]", field or ""):
                if title == _title_key(part):
                    return True
    return False


def is_docx_project_table_doc_type(doc_type: str | None) -> bool:
    return (doc_type or "").strip().upper() in _DOCX_PROJECT_TABLE_TYPES


def supplement_docx_career_table_projects(
    candidate: ProfileCandidateDocument,
    *,
    page_texts: Mapping[tuple[str, int], str] | None,
    document_type_by_id: Mapping[str, str],
    document_extension_by_id: Mapping[str, str] | None = None,
) -> tuple[ProfileCandidateDocument, dict[str, int]]:
    """Recover / correct verified DOCX table projects; drop employment fakes.

    Runs for DOC-CAREER and DOC-RESUME only when structured table rows parse
    successfully (header + dated project rows + literal original quotes).
    """
    metrics = {
        "table_rows": 0,
        "matched_rows": 0,
        "supplemented_rows": 0,
        "corrected_titles": 0,
        "dropped_employment_fakes": 0,
    }
    if not page_texts:
        return candidate, metrics

    verified: list[dict[str, Any]] = []
    for (document_id, page_no), text in sorted(page_texts.items()):
        doc_type = (document_type_by_id.get(document_id) or "").strip().upper()
        if not is_docx_project_table_doc_type(doc_type):
            continue
        ext = (document_extension_by_id or {}).get(document_id, "docx")
        if ext and ext.lower().lstrip(".") != "docx":
            continue
        for row in parse_career_table_rows_from_text(
            text, page_no=page_no, document_id=document_id
        ):
            verified.append(row)

    if not verified:
        return candidate, metrics

    metrics["table_rows"] = len(verified)
    projects = [project.model_copy(deep=True) for project in candidate.projects]
    matched_indexes: set[int] = set()

    for row in verified:
        row_months = _month_pair(row["start_date"], row["end_date"])
        match_idx = None
        # Prefer title-compatible period hits; else unique period(+customer).
        title_hit: int | None = None
        period_hits: list[int] = []
        for idx, project in enumerate(projects):
            if idx in matched_indexes:
                continue
            proj_months = _month_pair(project.start_date, project.end_date)
            if row_months is None or proj_months != row_months:
                continue
            if row.get("customer_name") and project.customer_name:
                if _match_key(row["customer_name"]) != _match_key(
                    project.customer_name
                ):
                    continue
            period_hits.append(idx)
            if _is_title_compatible(project.project_name, row["project_name"]):
                title_hit = idx
                break
        if title_hit is not None:
            match_idx = title_hit
        elif len(period_hits) == 1:
            match_idx = period_hits[0]

        doc_id = str(row.get("document_id") or "")
        ref = SourceRef(
            document_id=doc_id,
            page_no=int(row["page_no"]),
            quote_text=str(row.get("quote_text") or row["project_name"]),
        )
        if match_idx is None:
            projects.append(
                ProjectCandidate(
                    project_name=row["project_name"],
                    customer_name=row.get("customer_name"),
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    responsibilities=row.get("duty") or row.get("role"),
                    source_refs=[ref] if doc_id else [],
                )
            )
            metrics["supplemented_rows"] += 1
            matched_indexes.add(len(projects) - 1)
            continue

        matched_indexes.add(match_idx)
        metrics["matched_rows"] += 1
        project = projects[match_idx]
        updates: dict[str, Any] = {}
        verified_name = row["project_name"]
        if _flat(project.project_name) != verified_name:
            updates["project_name"] = verified_name
            metrics["corrected_titles"] += 1
        if not project.customer_name and row.get("customer_name"):
            updates["customer_name"] = row["customer_name"]
        if not project.responsibilities and (row.get("duty") or row.get("role")):
            updates["responsibilities"] = row.get("duty") or row.get("role")
        if doc_id:
            refs = list(project.source_refs or [])
            if ref not in refs:
                refs.append(ref)
                updates["source_refs"] = refs
        if updates:
            projects[match_idx] = project.model_copy(update=updates)

    verified_title_keys = {_title_key(row["project_name"]) for row in verified}
    verified_months = {
        _month_pair(row["start_date"], row["end_date"]) for row in verified
    }
    verified_document_ids = {str(row.get("document_id") or "") for row in verified}
    retained: list[ProjectCandidate] = []
    for project in projects:
        title = _title_key(project.project_name)
        months = _month_pair(project.start_date, project.end_date)
        in_table = title in verified_title_keys or (
            months is not None and months in verified_months
        )
        if title not in verified_title_keys and _is_proven_employment_mirror_on_page_one(
            project, candidate, verified_document_ids
        ):
            metrics["dropped_employment_fakes"] += 1
            continue
        if in_table:
            retained.append(project)
            continue
        retained.append(project)

    if (
        not metrics["supplemented_rows"]
        and not metrics["matched_rows"]
        and not metrics["corrected_titles"]
        and not metrics["dropped_employment_fakes"]
    ):
        return candidate, metrics
    return candidate.model_copy(update={"projects": retained}), metrics


__all__ = [
    "CAREER_TABLE_MARKER",
    "append_career_table_block",
    "attach_docx_career_tables_to_pages",
    "career_project_header_columns",
    "extract_docx_career_project_rows_from_page",
    "extract_docx_career_project_rows_from_docx",
    "extract_docx_career_project_rows_by_page",
    "extract_docx_career_project_rows_from_pdf",
    "format_career_table_block",
    "is_docx_project_table_doc_type",
    "literal_quote_from_page",
    "original_page_body",
    "parse_career_period",
    "parse_career_table_rows_from_text",
    "rows_from_layout",
    "supplement_docx_career_table_projects",
]
