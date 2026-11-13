"""Strict OOXML PPTX project-table rows for v30 Source-First.

Reads the original uploaded PPTX, not a lossy PDF preview. Extracts only
header-identified project tables, preserving independent row and source geometry.
Only empty rows, strictly verified header continuations, and explicitly
merged whole-table trailing notes may be skipped. Ambiguous project rows
fail the *whole* v30 candidate, preserving the v25 candidate/diffs.

No DB mutation, office conversion or model calls.
"""
from __future__ import annotations

import hashlib
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


def _frame_geometry(frame: ET.Element, rows: list[ET.Element]) -> list[list[float] | None]:
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
            or not row_heights or min(row_heights) < 0 or total <= 0):
        raise ValueError("V30_PPTX_TABLE_GEOMETRY_INVALID")

    boxes: list[list[float] | None] = []
    prior = 0
    for h in row_heights:
        # Zero-height OOXML placeholders have no defensible source bbox.
        # Later parsing may skip an independently verified structural row;
        # a project row with no bbox must fail the entire v30 attempt.
        if h == 0:
            boxes.append(None)
            continue
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


def _is_structural_separator(cells: list[str], columns: dict[str, int]) -> bool:
    """Recognize only structurally unambiguous non-project rows.

    No project title, customer, role or unmapped-column content may be present.
    A sole period-column label with no calendar year can be a section marker.
    An orphan date is NOT a section marker and must fail closed.
    """
    occupied = [index for index, value in enumerate(cells) if value]
    if not occupied:
        return True
    if occupied != [columns["period"]]:
        return False
    return not re.search(r"(?:19|20)\d{2}", cells[columns["period"]])


def _is_verified_trailing_footer(
    cells: list[str], columns: dict[str, int],
    *, row_index: int, table_row_count: int,
) -> bool:
    """Skip a final summary/annotation label only when it cannot be a project.

    The label must be the ONLY populated cell, in the unassigned leftmost
    column of the FINAL table row, without a year, and have an explicit
    Korean footer marker. Anything else remains ambiguous and fails closed.
    """
    if row_index != table_row_count - 1 or 0 in columns.values():
        return False
    if [i for i, value in enumerate(cells) if value] != [0]:
        return False
    label = cells[0]
    if len(label) > 80 or re.search(r"(?:19|20)\d{2}", label):
        return False
    return bool(re.match(
        r"^(?:합계|총계|소계|총합|총\s*(?:\d|건수|프로젝트)|비고\s*[:：]|참고\s*[:：]|주석\s*[:：])",
        label,
    ))


def _is_merged_secondary_header(
    cells: list[str],
    columns: dict[str, int],
    row_element: ET.Element,
    *,
    row_index: int,
    first_data: int,
) -> bool:
    """Recognize a row directly beneath its table header as header continuation.

    The source layout must explicitly mark BOTH project title and customer
    as vertically continued cells. This is not a general date/row skip.
    """
    if row_index != first_data or not any(cells):
        return False
    if any(re.search(r"(?:19|20)\d{2}", value) for value in cells):
        return False
    for field in ("project_name", "customer", "role"):
        idx = columns.get(field)
        if idx is not None and (idx >= len(cells) or cells[idx]):
            return False

    merged = {
        i for i, tc in enumerate(row_element.findall(f"{_A}tc"))
        if tc.get("vMerge") == "1"
    }
    return bool(
        columns["project_name"] in merged
        and columns.get("customer") in merged
    )


def _is_full_width_trailing_note(
    cells: list[str],
    columns: dict[str, int],
    row_element: ET.Element,
    *,
    row_index: int,
    table_row_count: int,
) -> bool:
    """Recognize only a last-row, whole-table merged note (not a project).

    One occupied first cell spans all table columns and all other cells
    are explicitly horizontal-merge continuations. No mapped identity or
    date may be present. Ambiguous non-merged text still fails closed.
    """
    if row_index != table_row_count - 1 or 0 in columns.values():
        return False
    if [i for i, value in enumerate(cells) if value] != [0]:
        return False
    if not 1 <= len(cells[0]) <= 80:
        return False
    if re.search(r"(?:19|20)\d{2}", cells[0]):
        return False
    if re.search(r"프로젝트|사업명|발주처|고객사|수행기간", cells[0]):
        return False

    elements = row_element.findall(f"{_A}tc")
    if len(elements) != len(cells) or len(elements) < 2:
        return False
    try:
        span = int(elements[0].get("gridSpan", "1"))
    except ValueError:
        return False
    return span == len(cells) and all(
        element.get("hMerge") == "1" for element in elements[1:]
    )


def pptx_native_table_rows(
    *,
    source_builder: AnalysisSourceBuilder,
    document: DocumentSnapshot,
) -> list[dict]:
    """Extract only verified PPTX table cells, mapped to document slide pages.

    Positive-height rows have bbox in PPTX slide points, not PDF points.
    Zero-height source rows have bbox=None and an exact OOXML
    slide/table/row/cell locator; geometry is never fabricated.
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

            table_index = 0
            for frame in root.findall(f".//{_P}graphicFrame"):
                table = frame.find(f".//{_A}tbl")
                if table is None:
                    continue
                table_index += 1

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
                    if header_columns(row) == columns:
                        continue  # Explicit repeated column header.
                    if _is_structural_separator(row, columns):
                        continue  # Blank row or period-column non-date label.
                    if _is_merged_secondary_header(
                        row, columns, row_elements[row_index],
                        row_index=row_index, first_data=first_data,
                    ):
                        continue  # Layout-verified continuation of the table header.
                    if _is_verified_trailing_footer(
                        row, columns, row_index=row_index,
                        table_row_count=len(cells),
                    ) or _is_full_width_trailing_note(
                        row, columns, row_elements[row_index],
                        row_index=row_index, table_row_count=len(cells),
                    ):
                        continue  # Explicit footer or whole-table merged note.

                    if len(row) <= max(columns.values()):
                        raise ValueError("V30_PPTX_INCOMPLETE_TABLE_ROW")
                    period = row[columns["period"]]
                    title = row[columns["project_name"]]
                    if (not re.search(r"(?:19|20)\d{2}", period)
                            or len(title) < 4):
                        raise ValueError("V30_PPTX_UNVERIFIED_PROJECT_ROW")

                    bbox = geometry[row_index]
                    row_height = int(row_elements[row_index].attrib["h"])
                    entry = _row(
                        str(document.id), page_no, "pptx-native-table",
                        row_index, row, columns, bbox,
                    )
                    # Table+row+original cell values form stable source
                    # identity even if OOXML specifies zero rendered height.
                    entry["row_id"] = hashlib.sha256(
                        (f"{document.id}|{page_no}|{table_index}|{row_index}|"
                         + "|".join(row)).encode("utf-8")
                    ).hexdigest()[:16]
                    entry["pptx_locator"] = {
                        "slide_no": page_no,
                        "table_index": table_index,
                        "row_index": row_index,
                        "period_column": columns["period"],
                        "project_name_column": columns["project_name"],
                        "customer_column": columns.get("customer"),
                        "row_height_emu": row_height,
                    }
                    entry["geometry_space"] = (
                        "pptx-ooxml-row" if bbox is None
                        else "pptx-slide-points"
                    )
                    out.append(entry)
                    if len(out) > _MAX_NATIVE_ROWS:
                        raise ValueError("V30_PPTX_ROW_LIMIT_EXCEEDED")
    finally:
        archive.close()

    if not out:
        raise ValueError("V30_PPTX_DOCUMENT_WITHOUT_NATIVE_ROWS")
    return out
