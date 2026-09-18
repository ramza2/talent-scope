"""Native HWP 5.x / HWPX text extraction adapter.

The application intentionally depends only on this small adapter instead of
calling parser libraries throughout business logic.

- HWPX: TalentScope reads OWPML ZIP/XML directly and preserves table rows.
- HWP 5.x: syhwp is used for native plain-text extraction.

This adapter is for content extraction only.  It never invents physical page
numbers or tries to reproduce Hancom layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import syhwp

from app.modules.document_processing.parsers.hwpx import (
    HwpxExtractionError,
    extract_hwpx_structured_text,
    is_hwpx_file,
)


class HancomExtractionError(Exception):
    """Safe, application-level error for native Hancom extraction failures."""


@dataclass(frozen=True)
class HancomTextExtraction:
    text: str
    detected_format: str
    parser_name: str = "syhwp"


def extract_hancom_text(path: Path) -> HancomTextExtraction:
    """Extract analysis text from an HWP 5.x or HWPX file.

    HWPX uses TalentScope's own lightweight OWPML parser so table-heavy resumes
    retain row/cell relationships without depending on a pagination/viewer
    library.  Non-HWPX input falls back to syhwp, which handles HWP 5.x.
    """
    if not path.is_file():
        raise HancomExtractionError("한글 원본 파일이 없습니다.")

    if is_hwpx_file(path):
        try:
            result = extract_hwpx_structured_text(path)
        except HwpxExtractionError as exc:
            raise HancomExtractionError("HWPX 텍스트 추출에 실패했습니다.") from exc
        return HancomTextExtraction(
            text=result.text,
            detected_format="hwpx",
            parser_name="owpml-xml",
        )

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

    return HancomTextExtraction(
        text=normalized,
        detected_format=str(detected),
        parser_name="syhwp",
    )


def _normalize_text(text: str | None) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    value = "".join(ch for ch in value if ch in {"\n", "\t"} or ord(ch) >= 32)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
