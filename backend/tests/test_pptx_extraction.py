"""Focused native PPTX extraction and LibreOffice fallback tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

from app.modules.document_processing.converters.base import ConverterError
from app.modules.document_processing.parsers.pptx import extract_pptx_text
from app.storage.base import StoredObject


class FakeStorage:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def get(self, key: str, *, byte_range=None) -> StoredObject:  # noqa: ANN001
        _ = (key, byte_range)
        return StoredObject(body=io.BytesIO(self.data), content_length=len(self.data))


class FakeConverter:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def convert_to_pdf(self, source_path: Path, work_dir: Path) -> Path:
        _ = (source_path, work_dir)
        self.calls += 1
        if self.fail:
            raise ConverterError("injected converter failure")
        raise AssertionError("converter should not be called")


def _identify_settings():
    return SimpleNamespace(
        identify_max_vlm_pages=3,
        identify_max_pages=5,
        identify_pdf_render_dpi=120,
    )


def _pptx_bytes() -> bytes:
    slide1 = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody><a:p><a:r><a:t>투입인력 이력사항</a:t></a:r></a:p></p:txBody></p:sp>
    <p:graphicFrame><a:graphic><a:graphicData><a:tbl>
      <a:tr>
        <a:tc><a:txBody><a:p><a:r><a:t>성 명</a:t></a:r></a:p></a:txBody></a:tc>
        <a:tc><a:txBody><a:p><a:r><a:t>홍 길 동</a:t></a:r></a:p></a:txBody></a:tc>
      </a:tr>
      <a:tr>
        <a:tc><a:txBody><a:p><a:r><a:t>담당업무</a:t></a:r></a:p></a:txBody></a:tc>
        <a:tc><a:txBody><a:p><a:r><a:t>애플리케이션 운영</a:t></a:r></a:p></a:txBody></a:tc>
      </a:tr>
    </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
  </p:spTree></p:cSld>
</p:sld>
"""
    slide2 = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:txBody><a:p><a:r><a:t>경력 사항</a:t></a:r></a:p></p:txBody></p:sp>
  </p:spTree></p:cSld>
</p:sld>
"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide1)
        archive.writestr("ppt/slides/slide2.xml", slide2)
    return out.getvalue()


def test_pptx_parser_preserves_slide_boundaries_and_table_rows(tmp_path):
    source = tmp_path / "profile.pptx"
    source.write_bytes(_pptx_bytes())

    result = extract_pptx_text(source)

    assert [slide.slide_no for slide in result.slides] == [1, 2]
    assert "투입인력 이력사항" in result.slides[0].text
    assert "성 명 | 홍 길 동" in result.slides[0].text
    assert "담당업무 | 애플리케이션 운영" in result.slides[0].text
    assert result.slides[1].text == "경력 사항"
    assert "[SLIDE 1]" in result.text
    assert "[SLIDE 2]" in result.text


def test_identity_uses_native_pptx_before_libreoffice():
    from app.modules.upload_identification.source_extractor import IdentitySourceExtractor

    converter = FakeConverter()
    row = SimpleNamespace(
        id="temp-pptx-1",
        original_filename="profile.pptx",
        document_type_code="DOC-PROFILE",
        extension="pptx",
        temp_storage_key="temp/key",
    )
    extractor = IdentitySourceExtractor(
        FakeStorage(_pptx_bytes()),
        settings=_identify_settings(),
        converter=converter,
        vlm=None,
    )

    bundle = extractor.extract_session_files([row])

    assert converter.calls == 0
    assert len(bundle.usable_sources) == 1
    assert "성 명 | 홍 길 동" in bundle.usable_sources[0].text
    assert bundle.usable_sources[0].page_count == 2
    assert bundle.usable_sources[0].vlm_pages_used == 0


def test_document_processing_pptx_falls_back_to_native_slide_text(tmp_path):
    from app.modules.document_processing.service import DocumentProcessingService

    svc = object.__new__(DocumentProcessingService)
    svc.converter = FakeConverter(fail=True)

    result = svc._convert_or_extract_pptx(
        extension="pptx",
        original_bytes=_pptx_bytes(),
        original_filename="profile.pptx",
        work_dir=tmp_path,
    )

    assert svc.converter.calls == 1
    assert result.preview_pdf_bytes is None
    assert result.page_count == 2
    assert [page.page_no for page in result.pages] == [1, 2]
    assert "성 명 | 홍 길 동" in (result.pages[0].extracted_text or "")
    assert result.pages[0].layout_json == {
        "source_format": "pptx",
        "native_parser": "ooxml-xml",
        "page_mapping": "SLIDE",
        "needs_vlm": False,
    }
