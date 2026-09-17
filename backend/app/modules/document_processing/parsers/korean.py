"""Native text extraction for Korean HWP/HWPX documents.

HWP 5.x is delegated to the small ``extract-hwp`` package behind this adapter.
HWPX is ZIP/XML, so TalentScope extracts paragraph text directly instead of
requiring LibreOffice just to identify/analyse a document.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

KOREAN_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({"hwp", "hwpx"})
_HWPX_SECTION_RE = re.compile(r"^Contents/section(\d+)\.xml$", re.IGNORECASE)
_MAX_HWPX_MEMBERS = 4096
_MAX_HWPX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024


class KoreanDocumentExtractionError(Exception):
    """Raised when a supported Korean document cannot yield usable text."""


def _normalise_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_hwp5_text(path: Path) -> str:
    """Extract text from a binary HWP 5.x file."""
    try:
        from extract_hwp import extract_text_from_hwp5
    except ImportError as exc:  # deployment/package error, not user content
        raise KoreanDocumentExtractionError("HWP parser dependency is unavailable") from exc

    try:
        text = extract_text_from_hwp5(str(path))
    except Exception as exc:
        raise KoreanDocumentExtractionError("HWP 5.x text extraction failed") from exc

    text = _normalise_text(text or "")
    if not text:
        raise KoreanDocumentExtractionError("HWP 5.x document contains no extractable text")
    return text


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def extract_hwpx_text(path: Path) -> str:
    """Extract paragraph text from an HWPX ZIP/XML document.

    This intentionally does not attempt page layout reconstruction. HWPX page
    rendering is approximate without Hancom's layout engine; TalentScope only
    needs faithful text for identity/profile analysis.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_HWPX_MEMBERS:
                raise KoreanDocumentExtractionError("HWPX contains too many archive members")
            if any(info.flag_bits & 0x1 for info in infos):
                raise KoreanDocumentExtractionError("Encrypted HWPX is not supported")
            total_size = sum(info.file_size for info in infos)
            if total_size > _MAX_HWPX_UNCOMPRESSED_BYTES:
                raise KoreanDocumentExtractionError("HWPX uncompressed size exceeds safety limit")

            sections: list[tuple[int, str]] = []
            for info in infos:
                match = _HWPX_SECTION_RE.match(info.filename)
                if match:
                    sections.append((int(match.group(1)), info.filename))
            if not sections:
                raise KoreanDocumentExtractionError("HWPX section XML was not found")

            paragraphs: list[str] = []
            for _index, member in sorted(sections):
                try:
                    root = ElementTree.fromstring(archive.read(member))
                except (ElementTree.ParseError, KeyError) as exc:
                    raise KoreanDocumentExtractionError("HWPX section XML is invalid") from exc
                for element in root.iter():
                    if _local_name(element.tag) != "p":
                        continue
                    pieces: list[str] = []
                    for child in element.iter():
                        if _local_name(child.tag) == "t" and child.text:
                            pieces.append(child.text)
                    paragraph = "".join(pieces).strip()
                    if paragraph:
                        paragraphs.append(paragraph)
    except KoreanDocumentExtractionError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise KoreanDocumentExtractionError("Invalid HWPX archive") from exc

    text = _normalise_text("\n".join(paragraphs))
    if not text:
        raise KoreanDocumentExtractionError("HWPX document contains no extractable text")
    return text


def extract_korean_document_text(path: Path, extension: str) -> str:
    ext = extension.lower().lstrip(".")
    if ext == "hwp":
        return extract_hwp5_text(path)
    if ext == "hwpx":
        return extract_hwpx_text(path)
    raise KoreanDocumentExtractionError(f"Unsupported Korean document extension: .{ext}")
