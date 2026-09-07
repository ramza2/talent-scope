"""LibreOffice / converter abstractions."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PdfConverter(Protocol):
    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        """Convert ``source_path`` to a PDF under ``work_dir``; return PDF path."""
        ...


class ConverterError(Exception):
    """Raised when preview PDF conversion fails."""
