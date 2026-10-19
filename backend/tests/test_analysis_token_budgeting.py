"""ModelOps /v1/token-count adaptive budgeting + PROJECT chunk merge."""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope",
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ["APP_ENV"] = "test"

_DOC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _caps(
    *,
    window: int,
    deployment: str = "dep-1",
    route: str = "route-1",
    routing: int = 100,
):
    from app.ai.providers.model_capabilities import ModelCapabilities

    return ModelCapabilities(
        model_id="qwen3-14b",
        context_window=window,
        supports_thinking=True,
        supports_tools=True,
        deployment_id=deployment,
        route_id=route,
        routing_version=routing,
        status="VERIFIED",
    )


def _count(
    *,
    prompt_tokens: int,
    window: int,
    deployment: str = "dep-1",
    route: str = "route-1",
    routing: int = 100,
):
    from app.ai.providers.token_count import TokenCountResult

    return TokenCountResult(
        model="qwen3-14b",
        prompt_tokens=prompt_tokens,
        context_window=window,
        status="VERIFIED",
        deployment_id=deployment,
        route_id=route,
        routing_version=routing,
    )


def test_budget_scales_with_context_window_ratios() -> None:
    from app.modules.analysis.token_budget import compute_prompt_chat_budget

    b_small = compute_prompt_chat_budget(
        _count(prompt_tokens=1000, window=4000),
        output_reserve_ratio=0.35,
        safety_margin_ratio=0.08,
        enable_thinking=False,
    )
    b_large = compute_prompt_chat_budget(
        _count(prompt_tokens=2000, window=8000),
        output_reserve_ratio=0.35,
        safety_margin_ratio=0.08,
        enable_thinking=False,
    )
    assert b_small.output_reserve_tokens * 2 == b_large.output_reserve_tokens
    assert b_small.safety_margin_tokens * 2 == b_large.safety_margin_tokens
    assert b_small.fits is True
    assert b_large.fits is True


def test_exact_prompt_tokens_change_budget_fit() -> None:
    from app.modules.analysis.token_budget import compute_prompt_chat_budget

    window = 4000
    # output=1400, safety=320 → room for prompt = 2280
    fit = compute_prompt_chat_budget(
        _count(prompt_tokens=2000, window=window),
        output_reserve_ratio=0.35,
        safety_margin_ratio=0.08,
        enable_thinking=False,
    )
    over = compute_prompt_chat_budget(
        _count(prompt_tokens=2500, window=window),
        output_reserve_ratio=0.35,
        safety_margin_ratio=0.08,
        enable_thinking=False,
    )
    assert fit.fits is True
    assert over.fits is False
    assert (
        fit.prompt_tokens + fit.output_reserve_tokens + fit.safety_margin_tokens
        <= fit.context_window
    )


def test_no_literal_context_or_estimator_in_budget_modules() -> None:
    from pathlib import Path

    files = [
        Path("app/modules/analysis/token_budget.py"),
        Path("app/ai/providers/token_count.py"),
        Path("app/ai/providers/model_capabilities.py"),
    ]
    banned_nums = ("8192", "12288", "16384")
    banned_est = ("chars_per_token", "estimate_text_tokens", "len(text) /")
    for path in files:
        text = path.read_text(encoding="utf-8")
        for banned in banned_nums:
            assert banned not in text, path
        for banned in banned_est:
            assert banned not in text, path


def test_token_count_payload_matches_chat_thinking(monkeypatch) -> None:
    import app.ai.providers.token_count as tc_mod
    from app.ai.providers.token_count import (
        build_token_count_payload,
        count_chat_prompt_tokens,
    )
    from app.core.config import Settings

    payload = build_token_count_payload(
        model="qwen3-14b",
        system_prompt="SYS",
        user_prompt="USER",
        enable_thinking=False,
    )
    assert payload["model"] == "qwen3-14b"
    assert payload["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "max_completion_tokens" not in payload
    assert "max_tokens" not in payload
    assert "tools" not in payload

    observed: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "object": "modelops.token_count",
                "model": "qwen3-14b",
                "prompt_tokens": 5017,
                "context_window": 9000,
                "status": "VERIFIED",
                "deployment_id": "dep",
                "route_id": "rte",
                "routing_version": 7,
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            assert url.endswith("/v1/token-count")
            observed.append(dict(json))
            return _Resp()

    monkeypatch.setattr(tc_mod.httpx, "Client", _Client)
    result = count_chat_prompt_tokens(
        Settings(
            llm_base_url="https://gateway.example.test",
            llm_api_key="k",
            llm_model="qwen3-14b",
        ),
        system_prompt="SYS",
        user_prompt="USER",
        enable_thinking=False,
    )
    assert observed[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert result.prompt_tokens == 5017
    assert result.route_id == "rte"


def test_token_count_fail_closed_statuses(monkeypatch) -> None:
    import pytest
    import app.ai.providers.token_count as tc_mod
    from app.ai.providers.errors import AIProviderError
    from app.ai.providers.token_count import count_chat_prompt_tokens
    from app.core.config import Settings

    settings = Settings(
        llm_base_url="https://gateway.example.test",
        llm_api_key="k",
        llm_model="qwen3-14b",
    )

    def _run(status_code=200, body=None):
        class _Resp:
            def __init__(self):
                self.status_code = status_code

            def json(self):
                return body or {}

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(tc_mod.httpx, "Client", _Client)
        with pytest.raises(AIProviderError):
            count_chat_prompt_tokens(
                settings,
                system_prompt="s",
                user_prompt="u",
                enable_thinking=False,
            )

    _run(status_code=422)
    _run(status_code=429)
    _run(status_code=503)
    _run(
        status_code=200,
        body={"status": "UNKNOWN", "prompt_tokens": 10, "context_window": 100},
    )
    _run(
        status_code=200,
        body={"status": "VERIFIED", "prompt_tokens": None, "context_window": 100},
    )


def test_capabilities_verified_and_fail_closed(monkeypatch) -> None:
    import pytest
    import app.ai.providers.model_capabilities as caps_mod
    from app.ai.providers.errors import AIProviderError
    from app.core.config import Settings

    class _Ok:
        status_code = 200

        def json(self):
            return {
                "id": "qwen3-14b",
                "status": "VERIFIED",
                "context_window": 9000,
                "supports_thinking": True,
                "supports_tools": True,
                "deployment_id": "dep-xyz",
                "route_id": "route-xyz",
                "routing_version": 100,
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            assert "/v1/models/" in url and url.endswith("/capabilities")
            return _Ok()

    monkeypatch.setattr(caps_mod.httpx, "Client", _Client)
    caps = caps_mod.fetch_model_capabilities(
        Settings(llm_base_url="https://g.test", llm_model="qwen3-14b", llm_api_key="k")
    )
    assert caps.route_id == "route-xyz"
    assert caps.context_window == 9000

    class _Bad:
        status_code = 503

        def json(self):
            return {}

    class _BadClient(_Client):
        def get(self, *a, **k):
            return _Bad()

    monkeypatch.setattr(caps_mod.httpx, "Client", _BadClient)
    with pytest.raises(AIProviderError):
        caps_mod.fetch_model_capabilities(
            Settings(llm_base_url="https://g.test", llm_model="m", llm_api_key="k")
        )


def test_route_change_detected() -> None:
    from app.modules.analysis.token_budget import token_count_route_changed

    a = _count(prompt_tokens=10, window=1000, deployment="d1", routing=1)
    b = _count(prompt_tokens=10, window=1000, deployment="d2", routing=1)
    c = _count(prompt_tokens=10, window=1000, deployment="d1", routing=2)
    d = _count(prompt_tokens=10, window=1000, route="r2")
    assert token_count_route_changed(a, a) is False
    assert token_count_route_changed(a, b) is True
    assert token_count_route_changed(a, c) is True
    assert token_count_route_changed(a, d) is True
    # Capability drift vs count (same identity fields → unchanged).
    same_caps = _caps(window=1000, deployment="d1", route="route-1", routing=1)
    assert token_count_route_changed(same_caps, a) is False
    assert token_count_route_changed(
        _caps(window=1000, deployment="other", route="route-1", routing=1), a
    ) is True


def test_complete_json_sends_max_completion_tokens(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings
    import app.ai.providers.llm as llm_module

    observed: list[dict] = []

    def fake_post(*, payload, **kwargs):
        observed.append(dict(payload))
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": '{"ok": true}'}}
            ],
            "model": payload.get("model"),
            "usage": {},
        }

    monkeypatch.setattr(llm_module, "post_chat_completions", fake_post)
    provider = OpenAICompatibleLLMProvider(
        Settings(
            llm_base_url="https://llm.test",
            llm_api_key="k",
            llm_model="m",
            analysis_llm_enable_thinking=False,
        )
    )
    provider.complete_json(
        system_prompt="s", user_prompt="u", max_completion_tokens=1200
    )
    assert observed[0]["max_completion_tokens"] == 1200
    assert observed[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_plan_uses_token_count_budget_callback_not_chars() -> None:
    from app.modules.analysis.token_budget import (
        compute_prompt_chat_budget,
        plan_project_source_chunks,
    )

    d1 = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\n" + ("A" * 40) + "\n"
    )
    d2 = (
        "[DOCUMENT D2]\nfilename: b.pptx\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\n" + ("B" * 40) + "\n"
    )
    blocks = d1 + d2
    calls: list[int] = []

    def budget_for_blocks(text: str):
        # Combined over budget; each document fits.
        prompt_tokens = 3000 if ("D1" in text and "D2" in text) else 500
        calls.append(prompt_tokens)
        return compute_prompt_chat_budget(
            _count(prompt_tokens=prompt_tokens, window=2000),
            output_reserve_ratio=0.35,
            safety_margin_ratio=0.08,
            enable_thinking=False,
        )

    planned = plan_project_source_chunks(
        blocks, budget_for_blocks=budget_for_blocks
    )
    assert len(planned) >= 2
    assert any("D1" in p and "D2" not in p for p in planned)
    assert any("D2" in p for p in planned)
    assert calls  # token-count callback used


def test_split_page_and_subchunk_helpers() -> None:
    from app.modules.analysis.token_budget import (
        split_block_by_pages,
        split_page_block_text,
    )

    block = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\none\n\n[PAGE 2]\ntwo\n"
    )
    pages = split_block_by_pages(block)
    assert len(pages) == 2
    huge = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n" + ("X" * 1000)
    )
    parts = split_page_block_text(huge, max_body_chars=300)
    assert len(parts) >= 3


def _proj(name, customer, start, end, *, doc, jobs=None, skills=None, biz=None):
    from app.ai.schemas.profile_candidate import (
        CodeRefCandidate,
        ProjectCandidate,
        SourceRef,
    )

    def rels(items):
        out = []
        for code, quote in items or []:
            out.append(
                CodeRefCandidate(
                    code=code,
                    raw_value=code,
                    source_refs=[
                        SourceRef(document_id=doc, page_no=1, quote_text=quote)
                    ],
                )
            )
        return out

    return ProjectCandidate(
        project_name=name,
        customer_name=customer,
        start_date=start,
        end_date=end,
        source_refs=[SourceRef(document_id=doc, page_no=1, quote_text=name)],
        jobs=rels(jobs),
        skills=rels(skills),
        business_domains=rels(biz),
    )


def _chunk_doc(projects):
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    return ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="테스터"),
        projects=projects,
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )


def test_merge_project_chunks_order_independent_and_unions_relations() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    p1 = _proj(
        "2017 HW1사업",
        "정부통합전산센터",
        "2017.10",
        "2018.03",
        doc=_DOC_A,
        jobs=[("JOB-MGT", "사업관리")],
        skills=[("TECH-SEC-AD", "AD")],
    )
    p2 = _proj(
        "2017 HW1사업",
        "정부통합전산센터",
        "2017.10",
        "2018.03",
        doc=_DOC_B,
        jobs=[("JOB-OPS-SYS", "시스템 운영")],
        biz=[("BIZ-PUBLIC", "공공")],
    )
    other = _proj(
        "별도 사업",
        "국방부",
        "2014.02",
        "2014.12",
        doc=_DOC_A,
        jobs=[("JOB-ARC-TA", "TA")],
    )
    forward = merge_project_chunk_candidates(
        [_chunk_doc([p1, other]), _chunk_doc([p2])]
    )
    reverse = merge_project_chunk_candidates(
        [_chunk_doc([p2]), _chunk_doc([other, p1])]
    )

    def view(doc):
        rows = []
        for p in doc.projects:
            rows.append(
                (
                    p.project_name,
                    p.customer_name,
                    p.start_date,
                    p.end_date,
                    tuple(sorted(j.code for j in p.jobs)),
                    tuple(sorted(s.code for s in p.skills)),
                    tuple(sorted(b.code for b in p.business_domains)),
                    tuple(
                        sorted(
                            (r.document_id, r.page_no, r.quote_text)
                            for r in p.source_refs
                        )
                    ),
                )
            )
        return sorted(rows)

    assert view(forward) == view(reverse)
    hw = next(p for p in forward.projects if p.project_name == "2017 HW1사업")
    assert {j.code for j in hw.jobs} == {"JOB-MGT", "JOB-OPS-SYS"}
    assert {s.code for s in hw.skills} == {"TECH-SEC-AD"}
    assert {b.code for b in hw.business_domains} == {"BIZ-PUBLIC"}
    assert {r.document_id for r in hw.source_refs} == {_DOC_A, _DOC_B}


def test_merge_does_not_collapse_distinct_projects() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj("A사업", "고객A", "2018.01", "2018.12", doc=_DOC_A)
    b = _proj("B사업", "고객A", "2018.01", "2018.12", doc=_DOC_B)
    out = merge_project_chunk_candidates([_chunk_doc([a]), _chunk_doc([b])])
    assert len(out.projects) == 2


def test_length_finish_reason_still_raises_truncated(monkeypatch) -> None:
    import pytest
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings
    import app.ai.providers.llm as llm_module

    def fake_post(*, payload, **kwargs):
        return {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"pr":[{"n":"truncated"}'},
                }
            ],
            "model": payload.get("model"),
            "usage": {"prompt_tokens": 5000, "completion_tokens": 3000},
        }

    monkeypatch.setattr(llm_module, "post_chat_completions", fake_post)
    provider = OpenAICompatibleLLMProvider(
        Settings(
            llm_base_url="https://llm.test",
            llm_api_key="k",
            llm_model="m",
            analysis_llm_enable_thinking=False,
        )
    )
    with pytest.raises(AIResponseTruncatedError) as exc:
        provider.complete_json(
            system_prompt="s", user_prompt="u", max_completion_tokens=100
        )
    assert exc.value.meta.get("finish_reason") == "length"


def test_second_pass_helper_semantics_unchanged_smoke() -> None:
    from app.modules.analysis.compact_v8 import supplement_projects_from_second_pass
    from app.ai.schemas.profile_candidate import (
        AnalysisMetaCandidate,
        ProfileCandidate,
        ProfileCandidateDocument,
        SummaryCandidate,
    )

    first = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="x"),
        projects=[
            _proj(
                "동일사업",
                "고객",
                "2020.01",
                "2020.12",
                doc=_DOC_A,
                skills=[("TECH-SEC-AD", "AD")],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    second = ProfileCandidateDocument(
        schema_version="profile-candidate-v1",
        profile=ProfileCandidate(name="x"),
        projects=[
            _proj(
                "동일사업",
                "고객",
                "2020.01",
                "2020.12",
                doc=_DOC_A,
                skills=[("TECH-SEC-NAC", "NAC")],
                jobs=[("JOB-MGT", "사업관리")],
            )
        ],
        summary=SummaryCandidate(),
        analysis=AnalysisMetaCandidate(),
    )
    out, n = supplement_projects_from_second_pass(first, second)
    assert n == 0
    assert out.projects[0].skills[0].code == "TECH-SEC-AD"
    assert out.projects[0].jobs == []


def test_provider_count_prompt_tokens_uses_thinking_toggle(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings
    import app.ai.providers.llm as llm_module
    from app.ai.providers.token_count import TokenCountResult

    seen: dict = {}

    def fake_count(settings, *, system_prompt, user_prompt, enable_thinking, **kwargs):
        seen["enable_thinking"] = enable_thinking
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        return TokenCountResult(
            model="m",
            prompt_tokens=12,
            context_window=4000,
            status="VERIFIED",
            deployment_id="d",
            route_id="r",
            routing_version=1,
        )

    monkeypatch.setattr(llm_module, "count_chat_prompt_tokens", fake_count)
    provider = OpenAICompatibleLLMProvider(
        Settings(
            llm_base_url="https://llm.test",
            llm_api_key="k",
            llm_model="m",
            analysis_llm_enable_thinking=False,
        )
    )
    provider.count_prompt_tokens(system_prompt="S", user_prompt="U")
    assert seen["enable_thinking"] is False
    assert seen["system"] == "S"
    assert seen["user"] == "U"
