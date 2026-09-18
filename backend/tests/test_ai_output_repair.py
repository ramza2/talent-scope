"""Focused regression tests for noisy AI JSON and identity normalization."""

from __future__ import annotations

from app.ai.providers.llm import FakeLLMProvider
from app.ai.schemas.identity import IdentityExtraction
from app.ai.utils.json_extract import parse_json_object


def test_identity_repair_prefers_final_json_object_and_compacts_korean_name() -> None:
    raw = """
<think>
{"name": "초안", "company": null, "phone": null, "email": null}
</think>
{"name": "곽   영   훈", "company": null, "phone": "010-5149-8418", "email": null}
"""
    identity = FakeLLMProvider(raw_content=raw).extract_identity(
        system_prompt="system",
        user_prompt="user",
    )

    assert identity.name == "곽영훈"
    assert identity.phone == "010-5149-8418"


def test_aggressive_json_scan_does_not_select_nested_object() -> None:
    raw = (
        'reasoning {"draft": {"name": "초안"}}\n'
        'final {"profile": {"name": "홍길동"}, "analysis": {"score": 0.8}}'
    )

    parsed = parse_json_object(raw, aggressive=True)

    assert parsed == {
        "profile": {"name": "홍길동"},
        "analysis": {"score": 0.8},
    }


def test_korean_name_spacing_is_compacted_but_non_korean_name_is_preserved() -> None:
    assert IdentityExtraction(name="강 상 원").name == "강상원"
    assert IdentityExtraction(name="남 궁 민").name == "남궁민"
    assert IdentityExtraction(name="John Smith").name == "John Smith"
