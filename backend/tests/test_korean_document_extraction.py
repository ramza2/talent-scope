"""HWP/HWPX native extraction and LibreOffice fallback regression tests."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.modules.document_processing.converters.base import ConverterError
from app.modules.document_processing.converters.libreoffice import LibreOfficeConverter
from app.modules.document_processing.parsers import korean
from app.modules.document_processing.parsers.korean import KoreanDocumentExtractionError
from app.modules.document_processing.parsers.pdf import build_minimal_pdf_with_text
from app.storage.base import StoredObject


class MemoryStorage:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    def get(self, key: str, *, byte_range=None) -> StoredObject:
        data = self.objects[key]
        return StoredObject(io.BytesIO(data), len(data))

    def put_bytes(self, key: str, data: bytes, *, content_type=None) -> None:
        self.objects[key] = data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeConverter:
    def __init__(self, pdf_bytes: bytes | None = None, *, fail: bool = False) -> None:
        self.pdf_bytes = pdf_bytes or b""
        self.fail = fail
        self.calls = 0

    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        self.calls += 1
        if self.fail:
            raise ConverterError("injected converter failure")
        out = work_dir / "converted.pdf"
        out.write_bytes(self.pdf_bytes)
        return out


def test_native_parser_normalizes_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "sample.hwp"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(
        korean,
        "_library_extract",
        lambda _path: ("홍길동\x00  \r\nPython\r\n\r\n\r\n\r\nFastAPI  ", None),
    )

    text = korean.extract_korean_document_text(source, extension="hwp")

    assert "\x00" not in text
    assert "홍길동" in text
    assert "Python" in text
    assert "FastAPI" in text
    assert "\r" not in text
    assert "\n\n\n\n" not in text


def test_native_parser_reports_library_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.hwpx"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(
        korean,
        "_library_extract",
        lambda _path: ("", "invalid package"),
    )

    with pytest.raises(KoreanDocumentExtractionError):
        korean.extract_korean_document_text(source, extension="hwpx")


def test_identify_hwp_uses_native_text_before_converter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.upload_identification import source_extractor as module
    from app.modules.upload_identification.source_extractor import IdentitySourceExtractor

    storage = MemoryStorage({"tmp/hwp": b"not-real-hwp"})
    converter = FakeConverter(fail=True)
    monkeypatch.setattr(
        module,
        "extract_korean_document_text",
        lambda _path, *, extension: "강상원\n010-3499-2542\npeter31jang@gmail.com",
    )
    row = SimpleNamespace(
        id="file-1",
        original_filename="resume.hwp",
        document_type_code="DOC-RESUME",
        extension="hwp",
        temp_storage_key="tmp/hwp",
    )

    result = IdentitySourceExtractor(storage, converter=converter)._extract_one(
        row, vlm_budget=3, log_context={}
    )

    assert result.error is None
    assert "강상원" in result.text
    assert result.page_count == 1
    assert result.vlm_pages_used == 0
    assert converter.calls == 0


def test_identify_hwpx_native_failure_falls_back_to_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.upload_identification import source_extractor as module
    from app.modules.upload_identification.source_extractor import IdentitySourceExtractor

    pdf = build_minimal_pdf_with_text("홍길동 이력서 010-1111-2222")
    storage = MemoryStorage({"tmp/hwpx": b"not-real-hwpx"})
    converter = FakeConverter(pdf)

    def fail_native(_path, *, extension):
        raise KoreanDocumentExtractionError("injected native failure")

    monkeypatch.setattr(module, "extract_korean_document_text", fail_native)
    row = SimpleNamespace(
        id="file-2",
        original_filename="resume.hwpx",
        document_type_code="DOC-RESUME",
        extension="hwpx",
        temp_storage_key="tmp/hwpx",
    )

    result = IdentitySourceExtractor(storage, converter=converter)._extract_one(
        row, vlm_budget=0, log_context={}
    )

    assert result.error is None
    assert "홍길동" in result.text
    assert converter.calls == 1


def test_document_hwp_converter_failure_uses_native_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.modules.document_processing import service as module
    from app.modules.document_processing.service import DocumentProcessingService

    converter = FakeConverter(fail=True)
    monkeypatch.setattr(
        module,
        "extract_korean_document_text",
        lambda _path, *, extension: "강상원\nJava\nSpring\nOracle",
    )
    svc = DocumentProcessingService(
        db=object(),
        storage=MemoryStorage(),
        settings=Settings(),
        converter=converter,
    )
    document = SimpleNamespace(original_filename="resume.hwp")

    result = svc._build_extraction(
        document=document,
        extension="hwp",
        original_bytes=b"not-real-hwp",
        work_dir=tmp_path,
    )

    assert result.page_count == 1
    assert result.preview_pdf_bytes is None
    assert result.uses_original_as_preview is False
    assert result.pages[0].extraction_method == "HWP_TEXT_PARSER"
    assert result.pages[0].layout_json == {
        "source_format": "HWP",
        "page_mapping": "UNAVAILABLE",
    }
    assert "Spring" in (result.pages[0].extracted_text or "")


def test_document_hwpx_converter_success_preserves_pdf_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.modules.document_processing import service as module
    from app.modules.document_processing.service import DocumentProcessingService

    pdf = build_minimal_pdf_with_text("HWPX converted text")
    converter = FakeConverter(pdf)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("native parser must not run when PDF conversion succeeds")

    monkeypatch.setattr(module, "extract_korean_document_text", should_not_run)
    svc = DocumentProcessingService(
        db=object(),
        storage=MemoryStorage(),
        settings=Settings(),
        converter=converter,
    )
    document = SimpleNamespace(original_filename="resume.hwpx")

    result = svc._build_extraction(
        document=document,
        extension="hwpx",
        original_bytes=b"not-real-hwpx",
        work_dir=tmp_path,
    )

    assert converter.calls == 1
    assert result.preview_pdf_bytes == pdf
    assert result.page_count >= 1


def test_document_korean_all_extractors_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.modules.document_processing import service as module
    from app.modules.document_processing.service import DocumentProcessingService

    converter = FakeConverter(fail=True)

    def fail_native(_path, *, extension):
        raise KoreanDocumentExtractionError("injected native failure")

    monkeypatch.setattr(module, "extract_korean_document_text", fail_native)
    svc = DocumentProcessingService(
        db=object(),
        storage=MemoryStorage(),
        settings=Settings(),
        converter=converter,
    )

    with pytest.raises(ConverterError, match="모두 실패"):
        svc._build_extraction(
            document=SimpleNamespace(original_filename="resume.hwp"),
            extension="hwp",
            original_bytes=b"not-real-hwp",
            work_dir=tmp_path,
        )


def test_libreoffice_missing_pdf_includes_captured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.modules.document_processing.converters import libreoffice as module

    source = tmp_path / "source.hwp"
    source.write_bytes(b"fixture")
    completed = SimpleNamespace(
        returncode=0,
        stdout=b"convert started",
        stderr=b"Error: source file could not be loaded",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: completed)
    converter = LibreOfficeConverter(Settings(libreoffice_bin="/bin/true"))

    with pytest.raises(ConverterError) as exc_info:
        converter.convert_to_pdf(source, tmp_path)

    message = str(exc_info.value)
    assert "convert started" in message
    assert "source file could not be loaded" in message
