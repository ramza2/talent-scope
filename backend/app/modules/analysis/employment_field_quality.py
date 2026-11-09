"""Conservative semantic checks for AI-extracted employer department fields.

These checks flag obvious field leakage; they never guess a replacement value
or rewrite a candidate's source evidence.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

DepartmentIssue = Literal["DATE", "POSITION"]

_DATE_PART = r"(?:19|20)\d{2}(?:[./-]\d{1,2}(?:[./-]\d{1,2})?)?"
_KOREAN_DATE_PART = r"(?:19|20)\d{2}\s*년(?:\s*\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?)?"
_DATE_ONLY_RE = re.compile(
    rf"(?:{_DATE_PART}|{_KOREAN_DATE_PART})"
    rf"(?:\s*[~～–—]\s*(?:{_DATE_PART}|{_KOREAN_DATE_PART}|현재))?"
)
_POSITION_ONLY = frozenset(
    {
        "사원", "주임", "대리", "과장", "차장", "부장", "팀장", "실장",
        "본부장", "이사", "상무", "상무이사", "전무", "전무이사",
        "부사장", "사장", "대표", "대표이사", "선임", "책임", "수석",
        "pm", "pl", "pm/pl",
    }
)


def employment_department_issue(value: object) -> DepartmentIssue | None:
    """Return a reason only for a standalone date or job title/role.

    Compound organization names such as ``경영지원팀``, ``상무실`` and
    ``PMO팀`` are intentionally allowed. Empty departments are also valid.
    """
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    if _DATE_ONLY_RE.fullmatch(text):
        return "DATE"
    if re.sub(r"\s+", "", text).casefold() in _POSITION_ONLY:
        return "POSITION"
    return None
