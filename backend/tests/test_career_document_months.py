"""Deterministic career_document_value → months parser tests."""

from __future__ import annotations

import pytest

from app.modules.analysis.career_document_months import (
    MAX_CAREER_MONTHS,
    MAX_CAREER_YEARS,
    parse_career_document_months,
)


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
        (f"{MAX_CAREER_YEARS}년", MAX_CAREER_YEARS * 12),
        (f"{MAX_CAREER_MONTHS}개월", MAX_CAREER_MONTHS),
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
        # Approximate / range — never exact months.
        "약 16년",
        "16년 이상",
        "10년 미만",
        "15년 내외",
        "11년 정도",
        "10년+",
        "10년 +",
        "최소 10년",
        "최대 10년",
        "기술경력 약 16년",
        "SW기술자 경력 16년 이상",
        # Unrealistic career length / calendar-year misread.
        "2012년",
        "1998년 입사",
        f"{MAX_CAREER_YEARS + 1}년",
        f"{MAX_CAREER_MONTHS + 1}개월",
        "100년",
    ],
)
def test_parse_career_document_months_unparseable(raw) -> None:
    assert parse_career_document_months(raw) is None  # type: ignore[arg-type]


def test_max_career_years_bound_is_explicit() -> None:
    assert MAX_CAREER_YEARS == 60
    assert MAX_CAREER_MONTHS == 720
    assert parse_career_document_months("60년") == 720
    assert parse_career_document_months("61년") is None
    assert parse_career_document_months("2012년") is None
