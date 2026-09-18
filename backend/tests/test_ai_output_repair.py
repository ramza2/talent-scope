"""Focused regression tests for noisy AI JSON and identity normalization."""

from __future__ import annotations

from app.ai.providers.llm import FakeLLMProvider, OpenAICompatibleLLMProvider
from app.ai.schemas.identity import IdentityExtraction
from app.core.config import Settings
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


def test_llm_provider_uses_separate_timeouts_for_identity_and_profile(monkeypatch) -> None:
    from app.ai.providers import llm as llm_module

    observed: list[float] = []

    def fake_post_chat_completions(**kwargs):
        observed.append(float(kwargs["timeout_seconds"]))
        content = (
            '{"name":"홍길동","company":null,"phone":null,"email":null}'
            if len(observed) == 1
            else '{"schema_version":"profile-candidate-v1"}'
        )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(
        llm_module,
        "post_chat_completions",
        fake_post_chat_completions,
    )

    settings = Settings(
        llm_base_url="https://llm.example.test",
        llm_api_key="",
        llm_model="test-model",
        ai_request_timeout_seconds=61,
        analysis_ai_request_timeout_seconds=181,
    )
    provider = OpenAICompatibleLLMProvider(settings)

    provider.extract_identity(system_prompt="system", user_prompt="identity")
    provider.complete_json(system_prompt="system", user_prompt="profile")

    assert observed == [61.0, 181.0]
