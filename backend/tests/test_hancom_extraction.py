"""Focused HWP/HWPX native extraction and fallback tests."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.document_processing.converters.base import ConverterError
from app.modules.document_processing.parsers.hancom import (
    HancomExtractionError,
    HancomTextExtraction,
    extract_hancom_text,
)
from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
from app.storage.base import StoredObject


class FakeStorage:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def get(self, key: str, *, byte_range=None) -> StoredObject:  # noqa: ANN001
        _ = (key, byte_range)
        return StoredObject(body=io.BytesIO(self.data), content_length=len(self.data))


class FakeConverter:
    def __init__(self, pdf_bytes: bytes, *, fail: bool = False) -> None:
        self.pdf_bytes = pdf_bytes
        self.fail = fail
        self.calls = 0

    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        self.calls += 1
        if self.fail:
            raise ConverterError("injected converter failure")
        out = work_dir / "converted.pdf"
        out.write_bytes(self.pdf_bytes)
        return out


def _identify_settings():
    return SimpleNamespace(
        identify_max_vlm_pages=3,
        identify_max_pages=5,
        identify_pdf_render_dpi=120,
    )


def test_hancom_adapter_normalizes_plain_text(tmp_path, monkeypatch):
    from app.modules.document_processing.parsers import hancom

    source = tmp_path / "resume.hwp"
    source.write_bytes(b"fake-hwp")
    monkeypatch.setattr(hancom.syhwp, "detect_format", lambda _path: "hwp5")
    monkeypatch.setattr(
        hancom.syhwp,
        "extract_text",
        lambda _path: "  홍길동\r\nPython\x00\n\n\nFastAPI  ",
    )

    result = extract_hancom_text(source)

    assert result.detected_format == "hwp5"
    assert result.text == "홍길동\nPython\n\nFastAPI"


def test_hancom_adapter_rejects_empty_text(tmp_path, monkeypatch):
    from app.modules.document_processing.parsers import hancom

    source = tmp_path / "resume.hwpx"
    source.write_bytes(b"fake-hwpx")
    monkeypatch.setattr(hancom.syhwp, "detect_format", lambda _path: "hwpx")
    monkeypatch.setattr(hancom.syhwp, "extract_text", lambda _path: "\x00\r\n\n")

    with pytest.raises(HancomExtractionError, match="추출 가능한 텍스트"):
        extract_hancom_text(source)


@pytest.mark.parametrize("extension,detected", [("hwp", "hwp5"), ("hwpx", "hwpx")])
def test_identity_uses_native_hancom_before_converter(
    extension, detected, monkeypatch
):
    from app.modules.upload_identification import source_extractor
    from app.modules.upload_identification.source_extractor import IdentitySourceExtractor

    converter = FakeConverter(build_minimal_pdf_with_text("fallback should not run"))
    monkeypatch.setattr(
        source_extractor,
        "extract_hancom_text",
        lambda _path: HancomTextExtraction(
            text="강상원\n010-3499-2542\npeter31jjang@gmail.com",
            detected_format=detected,
        ),
    )
    row = SimpleNamespace(
        id="temp-1",
        original_filename=f"resume.{extension}",
        document_type_code="DOC-RESUME",
        extension=extension,
        temp_storage_key="temp/key",
    )
    extractor = IdentitySourceExtractor(
        FakeStorage(b"hancom-bytes"),
        settings=_identify_settings(),
        converter=converter,
        vlm=None,
    )

    bundle = extractor.extract_session_files([row])

    assert converter.calls == 0
    assert len(bundle.usable_sources) == 1
    assert "강상원" in bundle.usable_sources[0].text
    assert bundle.usable_sources[0].vlm_pages_used == 0


def test_identity_native_failure_falls_back_to_pdf(monkeypatch):
    from app.modules.upload_identification import source_extractor
    from app.modules.upload_identification.source_extractor import IdentitySourceExtractor

    def fail_native(_path):
        raise HancomExtractionError("injected native failure")

    monkeypatch.setattr(source_extractor, "extract_hancom_text", fail_native)
    converter = FakeConverter(build_minimal_pdf_with_text("Converted HWP resume text"))
    row = SimpleNamespace(
        id="temp-2",
        original_filename="resume.hwp",
        document_type_code="DOC-RESUME",
        extension="hwp",
        temp_storage_key="temp/key",
    )
    extractor = IdentitySourceExtractor(
        FakeStorage(b"hancom-bytes"),
        settings=_identify_settings(),
        converter=converter,
        vlm=None,
    )

    bundle = extractor.extract_session_files([row])

    assert converter.calls == 1
    assert len(bundle.usable_sources) == 1
    assert "Converted HWP" in bundle.usable_sources[0].text


def test_document_processing_hancom_falls_back_without_fake_page_count(
    tmp_path, monkeypatch
):
    from app.modules.document_processing import service as service_module
    from app.modules.document_processing.service import DocumentProcessingService

    monkeypatch.setattr(
        service_module,
        "extract_hancom_text",
        lambda _path: HancomTextExtraction(
            text="한글 원문 전체 텍스트",
            detected_format="hwpx",
        ),
    )
    svc = object.__new__(DocumentProcessingService)
    svc.converter = FakeConverter(b"", fail=True)

    result = svc._convert_or_extract_hancom(
        extension="hwpx",
        original_bytes=b"hwpx-bytes",
        original_filename="resume.hwpx",
        work_dir=tmp_path,
    )

    assert result.preview_pdf_bytes is None
    assert result.page_count == 1
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.extraction_method == "TEXT_PARSER"
    assert page.extracted_text == "한글 원문 전체 텍스트"
    assert page.layout_json == {
        "source_format": "hwpx",
        "native_parser": "syhwp",
        "page_mapping": "UNAVAILABLE",
        "logical_page": True,
        "needs_vlm": False,
    }


def test_document_processing_keeps_pdf_preview_when_converter_succeeds(tmp_path, monkeypatch):
    from app.modules.document_processing import service as service_module
    from app.modules.document_processing.service import DocumentProcessingService

    def should_not_run(_path):
        raise AssertionError("native parser should not run when PDF conversion succeeds")

    monkeypatch.setattr(service_module, "extract_hancom_text", should_not_run)
    pdf = build_minimal_pdf_with_text("Converted HWP preview")
    svc = object.__new__(DocumentProcessingService)
    converter = FakeConverter(pdf)
    svc.converter = converter

    result = svc._convert_or_extract_hancom(
        extension="hwp",
        original_bytes=b"hwp-bytes",
        original_filename="resume.hwp",
        work_dir=tmp_path,
    )

    assert converter.calls == 1
    assert result.preview_pdf_bytes == pdf
    assert result.page_count == 1
    assert result.pages[0].extraction_method == "TEXT_PARSER"


def test_libreoffice_no_pdf_error_includes_stderr(tmp_path, monkeypatch):
    from app.modules.document_processing.converters import libreoffice
    from app.modules.document_processing.converters.libreoffice import LibreOfficeConverter

    source = tmp_path / "source.hwp"
    source.write_bytes(b"hwp")
    monkeypatch.setattr(libreoffice.shutil, "which", lambda _binary: "/usr/bin/soffice")
    monkeypatch.setattr(
        libreoffice.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"",
            stderr=b"Error: source file could not be loaded",
        ),
    )
    settings = SimpleNamespace(
        libreoffice_bin="soffice",
        libreoffice_timeout_seconds=120,
    )

    with pytest.raises(ConverterError, match="source file could not be loaded"):
        LibreOfficeConverter(settings).convert_to_pdf(source, tmp_path)
