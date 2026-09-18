"""Native PPTX OOXML text extraction.

This parser is intentionally text-only. It preserves slide boundaries and table
rows without attempting to recreate PowerPoint layout or rendering.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_MAX_SLIDE_XML_BYTES = 20 * 1024 * 1024


class PptxExtractionError(Exception):
    """PPTX OOXML cannot be parsed into usable text."""


@dataclass(frozen=True)
class PptxSlideText:
    slide_no: int
    text: str


@dataclass(frozen=True)
class PptxTextExtraction:
    slides: tuple[PptxSlideText, ...]
    parser_name: str = "ooxml-xml"

    @property
    def text(self) -> str:
        return "\n\n".join(
            f"[SLIDE {slide.slide_no}]\n{slide.text}" for slide in self.slides
        )


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def _paragraph_text(element: ElementTree.Element) -> str:
    return _clean_text(
        "".join(node.text or "" for node in element.iter(f"{_A}t"))
    )


def _table_lines(table: ElementTree.Element) -> list[str]:
    lines: list[str] = []
    for row in table.findall(f"./{_A}tr"):
        cells: list[str] = []
        for cell in row.findall(f"./{_A}tc"):
            parts = [_paragraph_text(p) for p in cell.iter(f"{_A}p")]
            cells.append(" ".join(part for part in parts if part).strip())
        if any(cells):
            lines.append(" | ".join(cells).rstrip())
    return lines


def _slide_lines(root: ElementTree.Element) -> list[str]:
    lines: list[str] = []

    def walk(element: ElementTree.Element) -> None:
        if element.tag == f"{_A}tbl":
            lines.extend(_table_lines(element))
            return
        if element.tag == f"{_A}p":
            text = _paragraph_text(element)
            if text:
                lines.append(text)
            return
        for child in list(element):
            walk(child)

    walk(root)
    return lines


def extract_pptx_text(source_path: Path) -> PptxTextExtraction:
    """Extract ordered slide text and table rows from a PPTX package."""
    try:
        with zipfile.ZipFile(source_path) as archive:
            slide_entries: list[tuple[int, str]] = []
            total_xml_bytes = 0
            for info in archive.infolist():
                match = _SLIDE_RE.fullmatch(info.filename)
                if not match:
                    continue
                total_xml_bytes += int(info.file_size)
                if total_xml_bytes > _MAX_SLIDE_XML_BYTES:
                    raise PptxExtractionError("PPTX slide XML size limit exceeded")
                slide_entries.append((int(match.group(1)), info.filename))

            if not slide_entries:
                raise PptxExtractionError("PPTX slide XML not found")

            slides: list[PptxSlideText] = []
            for slide_no, filename in sorted(slide_entries):
                raw = archive.read(filename)
                try:
                    root = ElementTree.fromstring(raw)
                except ElementTree.ParseError as exc:
                    raise PptxExtractionError(
                        f"PPTX slide XML parse failed: slide {slide_no}"
                    ) from exc
                text = "\n".join(_slide_lines(root)).strip()
                if text:
                    slides.append(PptxSlideText(slide_no=slide_no, text=text))
    except PptxExtractionError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise PptxExtractionError("PPTX package read failed") from exc

    if not slides:
        raise PptxExtractionError("PPTX contains no extractable text")
    return PptxTextExtraction(slides=tuple(slides))
