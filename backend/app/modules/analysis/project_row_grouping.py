"""Conservative, in-memory table-row grouping for the TalentScope pilot.

No cross-row inference: cell associations only come from one geometrically
identified PDF table row, or one validated VLM table-band row. No DB writes.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

FIELDS = ("period", "project_name", "role", "customer", "employer", "category")


def _flat(value: object) -> str:
    return " ".join(str(value or "").split())


def _label(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _flat(value)).lower()


def _field(value: object) -> str | None:
    key = _label(value)
    if not key:
        return None
    if any(x in key for x in ("참여기간", "수행기간", "근무기간", "프로젝트기간")):
        return "period"
    if any(x in key for x in ("사업명", "프로젝트명", "프로젝트수행명")):
        return "project_name"
    if any(x in key for x in ("담당업무", "담당역할", "수행역할")) or key == "역할":
        return "role"
    if any(x in key for x in ("발주처", "고객사", "발주기관")):
        return "customer"
    if "소속회사" in key or "소속기관" in key:
        return "employer"
    if key in ("유형", "사업유형"):
        return "category"
    return None


def header_columns(cells: list[object]) -> dict[str, int]:
    """A header must explicitly identify the period and project columns."""
    found: dict[str, int] = {}
    for index, cell in enumerate(cells):
        field = _field(cell)
        if field is not None and field not in found:
            found[field] = index
    return found if {"period", "project_name"} <= found.keys() else {}


def _row(doc_id: str, page_no: int, source: str, index: int,
         cells: list[object], columns: dict[str, int] | None,
         bbox: object = None) -> dict[str, Any]:
    values = [_flat(cell) for cell in cells]
    mapped = {
        field: values[column] for field, column in (columns or {}).items()
        if column < len(values)
    }
    # A VLM-only row is intentionally not assigned guessed column meanings.
    rect = None
    if bbox is not None:
        try:
            coords = [round(float(v), 2) for v in bbox]
            if len(coords) == 4 and coords[2] > coords[0] and coords[3] > coords[1]:
                rect = coords
        except (TypeError, ValueError):
            pass
    row_id = hashlib.sha256(
        f"{doc_id}:{page_no}:{source}:{index}:{'|'.join(values)}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "row_id": row_id,
        "document_id": doc_id,
        "page_no": page_no,
        "source": source,
        "row_index": index,
        "bbox": rect,
        "fields": mapped,
        "cells": values,
        "column_mapping_verified": bool(columns),
    }


def native_table_groups(
    page: object, doc_id: str, page_no: int, *, strict_rows: bool = False,
) -> list[dict[str, Any]]:
    """Read only strong header-identified native tables; fail closed otherwise.

    strict_rows is reserved for v30 runtime: partially recognized native
    project rows must trigger fallback instead of silently losing a project.
    """
    try:
        finder = getattr(page, "find_tables", None)
        if not callable(finder):
            return []
        tables = finder().tables
    except Exception:
        return []
    groups: list[dict[str, Any]] = []
    for table in tables:
        try:
            data = table.extract() or []
        except Exception:
            continue
        if not data or len(data) > 500:
            continue
        start = None
        columns: dict[str, int] = {}
        for i, cells in enumerate(data[:6]):
            if not isinstance(cells, (list, tuple)):
                continue
            columns = header_columns(list(cells))
            if columns:
                start = i + 1
                break
        if start is None:
            continue
        for index in range(start, len(data)):
            cells = data[index]
            if not isinstance(cells, (list, tuple)):
                continue
            if len(cells) <= max(columns.values()):
                continue
            period = _flat(cells[columns["period"]])
            title = _flat(cells[columns["project_name"]])
            # A fully explicit row is mandatory; don't join split rows.
            if not re.search(r"(?:19|20)\d{2}", period) or len(title) < 4:
                if strict_rows and (period or title):
                    raise ValueError("V30_UNVERIFIED_NATIVE_TABLE_ROW")
                continue
            bbox = None
            try:
                bbox = table.rows[index].bbox
            except (AttributeError, IndexError, TypeError):
                pass
            groups.append(_row(doc_id, page_no, "pdf-native-table", index,
                               list(cells), columns, bbox))
    return groups


def vlm_band_groups(text: str, *, doc_id: str, page_no: int,
                    first_row: int, last_row: int) -> list[dict[str, Any]]:
    """Rows already checked against band boundaries; no semantic assignments."""
    from app.modules.analysis.vlm_table_banding import (
        is_valid_table_band_transcription,
        normalize_table_band_transcription,
    )
    normalized = normalize_table_band_transcription(text)
    if not is_valid_table_band_transcription(
        normalized, expected_rows=last_row - first_row + 1
    ):
        return []
    output = []
    for index, line in enumerate(normalized.splitlines(), start=first_row):
        output.append(_row(doc_id, page_no, "vlm-table-band", index,
                           [cell.strip() for cell in line.strip("|").split("|")],
                           columns=None))
    return output
