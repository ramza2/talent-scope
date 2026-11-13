"""Strict OOXML PPTX project-table rows for v30 Source-First.

Reads the original uploaded PPTX, not a lossy PDF preview. Extracts only
header-identified project tables, preserving independent row and source geometry.
Any nonempty, incomplete project-table row fails the *whole* v30 candidate;
the calling service preserves its complete v25 candidate/diffs.

No DB mutation, office conversion or model calls.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from app.modules.analysis.project_row_grouping import (
    _flat,
    _row,
    header_columns,
)
from app.modules.analysis.source_builder import AnalysisSourceBuilder, DocumentSnapshot

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_SLIDE = re.compile(r"ppt/slides/slide([1-9][0-9]*)\.xml\Z")
_EMU_PER_POINT = 12700
_MAX_PPTX_BYTES = 80 * 1024 * 1024
_MAX_SLIDE_XML_BYTES = 8 * 1024 * 1024
_MAX_ZIP_MEMBERS = 4000
_MAX_NATIVE_ROWS = 500


def _read_bounded_original(builder: AnalysisSourceBuilder, key: str) -> bytes:
    stream = builder.storage.get(key)
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in stream.iter_chunks():
            size += len(chunk)
            if size > _MAX_PPTX_BYTES:
                raise ValueError("V30_PPTX_SIZE_LIMIT")
            chunks.append(chunk)
    finally:
        stream.close()
    if not size:
        raise ValueError("V30_PPTX_EMPTY_ORIGINAL")
    return b"".join(chunks)


def _frame_geometry(frame: ET.Element, rows: list[ET.Element]) -> list[list[float]]:
    xfrm = frame.find(f"{_P}xfrm")
    if xfrm is None:
        raise ValueError("V30_PPTX_TABLE_GEOMETRY_MISSING")
    offset = xfrm.find(f"{_A}off")
    extent = xfrm.find(f"{_A}ext")
    if offset is None or extent is None:
        raise ValueError("V30_PPTX_TABLE_GEOMETRY_MISSING")

    try:
        x, y = int(offset.attrib["x"]), int(offset.attrib["y"])
        width, height = int(extent.attrib["cx"]), int(extent.attrib["cy"])
        row_heights = [int(r.attrib["h"]) for r in rows]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("V30_PPTX_TABLE_GEOMETRY_INVALID") from exc

    total = sum(row_heights)
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or not row_heights or min(row_heights) <= 0 or total <= 0):
        raise ValueError("V30_PPTX_TABLE_GEOMETRY_INVALID")

    boxes = []
    prior = 0
    for h in row_heights:
        y0 = y + height * prior / total
        y1 = y + height * (prior + h) / total
        bbox = [
            round(x / _EMU_PER_POINT, 2),
            round(y0 / _EMU_PER_POINT, 2),
            round((x + width) / _EMU_PER_POINT, 2),
            round(y1 / _EMU_PER_POINT, 2),
        ]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError("V30_PPTX_ROW_GEOMETRY_INVALID")
        boxes.append(bbox)
        prior += h
    return boxes


def _table_rows(table: ET.Element) -> tuple[list[ET.Element], list[list[str]]]:
    elements = list(table.findall(f"{_A}tr"))
    cells = [
        [
            _flat(" ".join(part.text or "" for part in cell.iter(f"{_A}t")))
            for cell in row.findall(f"{_A}tc")
        ]
        for row in elements
    ]
    return elements, cells


def pptx_native_table_rows(
    *,
    source_builder: AnalysisSourceBuilder,
    document: DocumentSnapshot,
) -> list[dict]:
    """Extract only verified PPTX table cells, mapped to document slide pages.

    BBox coordinates are in PPTX *slide points*, not PDF page points;
    source/geometry_space identify that distinction in provenance.
    """
    if (document.extension or "").lower().strip() != "pptx":
        raise ValueError("V30_PPTX_EXTENSION_MISMATCH")

    raw = _read_bounded_original(source_builder, document.storage_key)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ValueError("V30_PPTX_INVALID_ARCHIVE") from exc

    out: list[dict] = []
    try:
        members = archive.infolist()
        if len(members) > _MAX_ZIP_MEMBERS:
            raise ValueError("V30_PPTX_ZIP_MEMBER_LIMIT")
        slides = {}
        for item in members:
            match = _SLIDE.fullmatch(item.filename)
            if match:
                slide_no = int(match[1])
                if slide_no in slides or item.file_size > _MAX_SLIDE_XML_BYTES:
                    raise ValueError("V30_PPTX_INVALID_SLIDE")
                slides[slide_no] = item

        page_numbers = [p.page_no for p in document.pages]
        if (not slides or sorted(slides) != sorted(page_numbers)
                or len(page_numbers) != len(set(page_numbers))):
            raise ValueError("V30_PPTX_SLIDE_PAGE_MISMATCH")

        for page_no in sorted(slides):
            try:
                root = ET.fromstring(archive.read(slides[page_no]))
            except (ET.ParseError, zipfile.BadZipFile, RuntimeError) as exc:
                raise ValueError("V30_PPTX_INVALID_SLIDE_XML") from exc

            for frame in root.findall(f".//{_P}graphicFrame"):
                table = frame.find(f".//{_A}tbl")
                if table is None:
                    continue

                row_elements, cells = _table_rows(table)
                columns = {}
                first_data = None
                for index, candidate in enumerate(cells[:6]):
                    mapping = header_columns(candidate)
                    if mapping:
                        columns = mapping
                        first_data = index + 1
                        break
                if first_data is None:
                    continue

                geometry = _frame_geometry(frame, row_elements)
                for row_index in range(first_data, len(cells)):
                    row = cells[row_index]
                    if not any(row):
                        continue  # Entirely blank separator is not a project.
                    if header_columns(row) == columns:
                        continue  # Explicit repeated column header.

                    if len(row) <= max(columns.values()):
                        raise ValueError("V30_PPTX_INCOMPLETE_TABLE_ROW")
                    period = row[columns["period"]]
                    title = row[columns["project_name"]]
                    if (not re.search(r"(?:19|20)\d{2}", period)
                            or len(title) < 4):
                        raise ValueError("V30_PPTX_UNVERIFIED_PROJECT_ROW")

                    entry = _row(
                        str(document.id), page_no, "pptx-native-table",
                        row_index, row, columns, geometry[row_index],
                    )
                    entry["geometry_space"] = "pptx-slide-points"
                    out.append(entry)
                    if len(out) > _MAX_NATIVE_ROWS:
                        raise ValueError("V30_PPTX_ROW_LIMIT_EXCEEDED")
    finally:
        archive.close()

    if not out:
        raise ValueError("V30_PPTX_DOCUMENT_WITHOUT_NATIVE_ROWS")
    return out
