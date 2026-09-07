"""LibreOffice headless PDF converter."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.core.config import Settings, get_settings
from app.modules.document_processing.converters.base import ConverterError

logger = logging.getLogger(__name__)


class LibreOfficeConverter:
    """Convert Office/HWP documents to PDF via LibreOffice argv subprocess.

    Uses a unique UserInstallation directory per invocation so concurrent
    workers do not share profiles. ``shell=True`` is never used.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        if not source_path.is_file():
            raise ConverterError(f"원본 파일이 없습니다: {source_path}")

        binary = self.settings.libreoffice_bin
        if not shutil.which(binary) and not Path(binary).is_file():
            raise ConverterError(f"LibreOffice 실행 파일을 찾을 수 없습니다: {binary}")

        out_dir = work_dir / "lo_out"
        profile_dir = work_dir / "lo_profile"
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # file:// URI for UserInstallation (LibreOffice requirement).
        profile_uri = profile_dir.resolve().as_uri()
        cmd = [
            binary,
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir.resolve()),
            str(source_path.resolve()),
        ]
        timeout = int(self.settings.libreoffice_timeout_seconds)
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConverterError(
                f"LibreOffice 변환 시간 초과({timeout}s)"
            ) from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or b"").decode("utf-8", errors="replace")[:800]
            raise ConverterError(
                f"LibreOffice 변환 실패(code={completed.returncode}): {stderr}"
            )

        pdfs = sorted(out_dir.glob("*.pdf"))
        if not pdfs:
            raise ConverterError("LibreOffice 변환 결과 PDF가 없습니다.")
        return pdfs[0]
