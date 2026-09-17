"""Native text extraction for Korean HWP/HWPX documents.

The third-party parser is intentionally isolated behind this adapter so the
rest of TalentScope is not coupled to its API. HWP 5.x and HWPX are treated as
text-analysis formats here; browser preview/PDF rendering remains a separate
concern.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_KOREAN_EXTENSIONS = frozenset({"hwp", "hwpx"})


class KoreanDocumentExtractionError(Exception):
    """Raised when native HWP/HWPX text extraction cannot produce usable text."""


def _library_extract(path: Path) -> tuple[str, str | None]:
    """Call extract-hwp lazily to keep the dependency behind this adapter."""
    from extract_hwp import extract_text_from_hwp

    return extract_text_from_hwp(str(path))


def normalize_korean_document_text(text: str) -> str:
    """Normalize parser output without summarizing or reordering content."""
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "")
    value = "".join(
        ch
        for ch in value
        if ch in {"\n", "\t"} or ord(ch) >= 32
    )
    # Trim trailing whitespace but keep paragraph/line structure.
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def extract_korean_document_text(path: Path, *, extension: str | None = None) -> str:
    """Extract plain text from HWP 5.x or HWPX using ``extract-hwp``.

    The parser returns one logical text stream. It does not claim physical page
    mapping; callers that persist pages must mark page mapping as unavailable.
    """
    ext = (extension or path.suffix.lstrip(".")).lower().strip()
    if ext not in SUPPORTED_KOREAN_EXTENSIONS:
        raise KoreanDocumentExtractionError(
            f"지원하지 않는 한글 문서 확장자입니다: .{ext or '?'}"
        )
    if not path.is_file():
        raise KoreanDocumentExtractionError("한글 문서 원본 파일이 없습니다.")

    try:
        text, error = _library_extract(path)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise KoreanDocumentExtractionError(
            f"{ext.upper()} 텍스트 추출에 실패했습니다."
        ) from exc
    except Exception as exc:
        logger.info(
            "native korean document extraction exception extension=%s err=%s",
            ext,
            type(exc).__name__,
        )
        raise KoreanDocumentExtractionError(
            f"{ext.upper()} 텍스트 추출에 실패했습니다."
        ) from exc

    if error:
        logger.info(
            "native korean document extraction reported error extension=%s reason=%s",
            ext,
            str(error)[:300],
        )
        raise KoreanDocumentExtractionError(
            f"{ext.upper()} 텍스트 추출에 실패했습니다."
        )

    normalized = normalize_korean_document_text(text or "")
    if not normalized:
        raise KoreanDocumentExtractionError(
            f"{ext.upper()} 문서에서 사용 가능한 텍스트를 찾지 못했습니다."
        )
    return normalized
