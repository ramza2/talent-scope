"""Lightweight HWPX (OWPML) structured text extraction.

HWPX is a ZIP package containing OWPML XML.  TalentScope does not attempt to
reproduce Hancom pagination/layout here; it only preserves useful document
structure for AI/search input.  In particular, table rows are rendered as
pipe-separated text so resumes keep date / organization / description
relationships better than a flat stream of text nodes.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

_SECTION_RE = re.compile(r"^Contents/section(\d+)\.xml$", re.IGNORECASE)
_MAX_SECTION_XML_BYTES = 20 * 1024 * 1024


class HwpxExtractionError(Exception):
    """Safe application-level error for HWPX package/XML failures."""


@dataclass(frozen=True)
class HwpxStructuredText:
    text: str
    section_count: int
    table_count: int


def is_hwpx_file(path: Path) -> bool:
    """Return True when the file looks like an HWPX OWPML package."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return any(_SECTION_RE.match(name) for name in archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False


def extract_hwpx_structured_text(path: Path) -> HwpxStructuredText:
    """Extract paragraphs and table rows from an HWPX package.

    Physical pages are intentionally ignored.  OWPML stores document structure,
    not a reliable browser/server pagination result, so callers must not derive a
    physical page count from this extraction.
    """
    if not path.is_file():
        raise HwpxExtractionError("HWPX 원본 파일이 없습니다.")

    try:
        with zipfile.ZipFile(path) as archive:
            sections = _section_names(archive)
            if not sections:
                raise HwpxExtractionError("HWPX section XML을 찾을 수 없습니다.")

            lines: list[str] = []
            table_count = 0
            for section_name in sections:
                info = archive.getinfo(section_name)
                if info.file_size > _MAX_SECTION_XML_BYTES:
                    raise HwpxExtractionError("HWPX section XML이 너무 큽니다.")
                root = ElementTree.fromstring(archive.read(section_name))
                section_lines, section_tables = _extract_section(root)
                if section_lines:
                    if lines:
                        lines.append("")
                    lines.extend(section_lines)
                table_count += section_tables
    except HwpxExtractionError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise HwpxExtractionError("HWPX 구조를 읽을 수 없습니다.") from exc

    text = _normalize_lines(lines)
    if not text:
        raise HwpxExtractionError("HWPX에서 추출 가능한 텍스트가 없습니다.")
    return HwpxStructuredText(
        text=text,
        section_count=len(sections),
        table_count=table_count,
    )


def _section_names(archive: zipfile.ZipFile) -> list[str]:
    indexed: list[tuple[int, str]] = []
    for name in archive.namelist():
        match = _SECTION_RE.match(name)
        if match:
            indexed.append((int(match.group(1)), name))
    indexed.sort(key=lambda item: item[0])
    return [name for _index, name in indexed]


def _extract_section(root: ElementTree.Element) -> tuple[list[str], int]:
    parent = {child: node for node in root.iter() for child in node}
    lines: list[str] = []
    table_count = 0

    # ElementTree.iter() is document order.  Paragraphs inside tables are
    # handled by the table renderer and skipped here to avoid duplication.
    for element in root.iter():
        name = _local_name(element.tag)
        if name == "p" and not _has_ancestor(element, parent, "tbl"):
            paragraph = _paragraph_text_outside_tables(element, parent)
            if paragraph:
                lines.append(paragraph)
        elif name == "tbl" and not _has_ancestor(element, parent, "tbl"):
            table_count += 1
            lines.extend(_table_rows(element))

    return lines, table_count


def _paragraph_text_outside_tables(
    paragraph: ElementTree.Element,
    parent: dict[ElementTree.Element, ElementTree.Element],
) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if _local_name(node.tag) != "t" or _has_ancestor(node, parent, "tbl"):
            continue
        if node.text:
            parts.append(node.text)
    return _normalize_inline("".join(parts))


def _table_rows(table: ElementTree.Element) -> list[str]:
    rows: list[str] = []
    for row in table:
        if _local_name(row.tag) != "tr":
            continue
        cells: list[str] = []
        for cell in row:
            if _local_name(cell.tag) != "tc":
                continue
            text = _normalize_inline(
                " ".join(
                    node.text or ""
                    for node in cell.iter()
                    if _local_name(node.tag) == "t"
                )
            )
            cells.append(text)

        # Empty outer cells are layout artifacts in many resume templates.
        while cells and not cells[0]:
            cells.pop(0)
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _has_ancestor(
    element: ElementTree.Element,
    parent: dict[ElementTree.Element, ElementTree.Element],
    local_name: str,
) -> bool:
    current = parent.get(element)
    while current is not None:
        if _local_name(current.tag) == local_name:
            return True
        current = parent.get(current)
    return False


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_inline(value: str) -> str:
    value = value.replace("\x00", " ")
    return re.sub(r"\s+", " ", value).strip()


def _normalize_lines(lines: list[str]) -> str:
    cleaned: list[str] = []
    blank = False
    for line in lines:
        value = line.strip()
        if not value:
            if cleaned and not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(value)
        blank = False
    return "\n".join(cleaned).strip()
