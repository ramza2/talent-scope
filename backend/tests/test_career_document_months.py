"""Deterministic career_document_value → months parser tests."""

from __future__ import annotations

import pytest

from app.modules.analysis.career_document_months import parse_career_document_months


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("11년 10개월", 142),
        ("16년", 192),
        ("10개월", 10),
        ("기술경력 16년", 192),
        ("SW기술자 경력 12년 4개월", 148),
        ("  13년 8개월  ", 164),
        ("11년10개월", 142),
        ("0년 6개월", 6),
        ("0개월", 0),
    ],
)
def test_parse_career_document_months_supported(raw: str, expected: int) -> None:
    assert parse_career_document_months(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "경력 없음",
        "약 십육년",
        "11.5년",
        "11년 14개월",  # months >= 12 is ambiguous
        "11년 또는 12년",
        "16년 및 10개월",
        "abc",
        123,
        {"value": "16년"},
    ],
)
def test_parse_career_document_months_unparseable(raw) -> None:
    assert parse_career_document_months(raw) is None  # type: ignore[arg-type]
