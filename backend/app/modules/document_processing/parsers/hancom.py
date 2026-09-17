"""Native HWP 5.x / HWPX text extraction adapter.

The application intentionally depends only on this small adapter instead of
calling the third-party parser throughout business logic.  HWP/HWPX native
text extraction is used to keep identity/profile analysis working when
LibreOffice cannot render a Hancom document to PDF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import syhwp


class HancomExtractionError(Exception):
    """Safe, application-level error for native Hancom extraction failures."""


@dataclass(frozen=True)
class HancomTextExtraction:
    text: str
    detected_format: str


def extract_hancom_text(path: Path) -> HancomTextExtraction:
    """Extract plain text from an HWP 5.x or HWPX file.

    ``syhwp`` auto-detects the actual container format, so a misleading file
    extension does not silently select the wrong parser.  Third-party errors
    are wrapped so callers do not depend on library-specific exception types.
    """
    if not path.is_file():
        raise HancomExtractionError("한글 원본 파일이 없습니다.")

    try:
        detected = syhwp.detect_format(path)
        text = syhwp.extract_text(path)
    except syhwp.SyhwpError as exc:
        raise HancomExtractionError("한글 문서 텍스트 추출에 실패했습니다.") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HancomExtractionError("한글 문서를 읽을 수 없습니다.") from exc

    normalized = _normalize_text(text)
    if not normalized:
        raise HancomExtractionError("한글 문서에서 추출 가능한 텍스트가 없습니다.")

    return HancomTextExtraction(text=normalized, detected_format=str(detected))


def _normalize_text(text: str | None) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = "".join(ch for ch in value if ch in {"\n", "\t"} or ord(ch) >= 32)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
