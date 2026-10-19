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
    # root + each document — no unbounded body recursion.
    assert len(calls) <= 3


def test_planner_bounds_token_counts_for_huge_single_page() -> None:
    from app.modules.analysis.token_budget import (
        compute_prompt_chat_budget,
        plan_project_source_chunks,
    )

    huge = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\n" + ("HUGE " * 5000)
    )
    calls = {"n": 0}

    def budget_for_blocks(text: str):
        calls["n"] += 1
        # Always over-budget so a recursive planner would fan out.
        return compute_prompt_chat_budget(
            _count(prompt_tokens=9000, window=2000),
            output_reserve_ratio=0.35,
            safety_margin_ratio=0.08,
            enable_thinking=False,
        )

    planned = plan_project_source_chunks(huge, budget_for_blocks=budget_for_blocks)
    assert planned == [huge]
    # root + single document only (page leaf deferred to extract).
    assert calls["n"] == 2


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


def test_subchunk_preserves_original_page_marker_on_all_parts() -> None:
    from app.modules.analysis.token_budget import split_page_block_text

    block = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 3]\n" + ("BODYTEXT " * 200)
    )
    parts = split_page_block_text(block, max_body_chars=120)
    assert len(parts) >= 2
    for part in parts:
        assert part.startswith(
            "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
            "content:\n[PAGE 3]\n"
        )
        # Must not invent a different page number.
        assert "[PAGE 1]" not in part
        assert "[PAGE 2]" not in part
        assert part.count("[PAGE 3]") == 1


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


# ---------------------------------------------------------------------------
# Service-level adaptive PROJECT regressions (no DB)
# ---------------------------------------------------------------------------


def _doc_block(doc_id: str, *, alias_body: str, pages: str) -> str:
    return (
        f"[DOCUMENT]\n"
        f"document_id: {doc_id}\n"
        f"filename: {doc_id[:8]}.pdf\n"
        f"document_type: DOC-PROFILE\n"
        f"content:\n{pages}"
    )


def _core_payload() -> dict:
    return {
        "p": {"n": "홍길동", "tg": "EXPERT", "ac": "ABC테크", "cdv": "10년"},
        "j": [{"v": "PL", "c": "JOB-MGT-PL", "t": "PRIMARY"}],
        "s": [{"v": "Python", "c": "TECH-LANG-PYTHON"}],
        "x": [{"v": "사업관리", "c": "EXP-MGT"}],
        "w": [{"co": "ABC테크", "ti": "책임", "s": "2018-01", "e": "2024-12"}],
        "e": [{"sc": "서울대", "mj": "컴공", "dg": "학사"}],
        "c": [{"n": "정보처리기사", "acq": "2010.12"}],
        "conf": 0.9,
    }


def _projects_payload(name: str = "복구프로젝트-1") -> dict:
    return {
        "pr": [
            {
                "n": name,
                "cust": "고객-1",
                "s": "2020-01",
                "e": "2021-12",
                "resp": "PL 운영",
                "r": [{"d": "D1", "p": 1, "q": name}],
            }
        ]
    }


class _BudgetLLM:
    """Controllable LLM for staged CORE/PROJECT adaptive budget tests."""

    def __init__(
        self,
        *,
        count_fn,
        chat_fn=None,
        core_payload=None,
        project_payloads=None,
    ) -> None:
        self.count_fn = count_fn
        self.chat_fn = chat_fn
        self.core_payload = core_payload or _core_payload()
        self.project_payloads = list(project_payloads or [_projects_payload()])
        self.project_chat_i = 0
        self.count_calls = 0
        self.chat_calls: list[dict] = []
        self.count_log: list[dict] = []

    def count_prompt_tokens(self, *, system_prompt, user_prompt, log_context=None):
        self.count_calls += 1
        ctx = dict(log_context or {})
        self.count_log.append(ctx)
        return self.count_fn(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            log_context=ctx,
            call_index=self.count_calls,
        )

    def complete_json(
        self,
        *,
        system_prompt,
        user_prompt,
        log_context=None,
        max_completion_tokens=None,
    ):
        ctx = dict(log_context or {})
        self.chat_calls.append(
            {
                "phase": ctx.get("phase"),
                "max_completion_tokens": max_completion_tokens,
                "recovery_retry": ctx.get("recovery_retry"),
                "split_depth": ctx.get("split_depth"),
                "chunk_index": ctx.get("chunk_index"),
                "user_prompt": user_prompt,
            }
        )
        if self.chat_fn is not None:
            return self.chat_fn(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                log_context=ctx,
                max_completion_tokens=max_completion_tokens,
            )
        phase = ctx.get("phase")
        if phase == "core":
            return dict(self.core_payload)
        if self.project_chat_i >= len(self.project_payloads):
            return dict(self.project_payloads[-1])
        payload = self.project_payloads[self.project_chat_i]
        self.project_chat_i += 1
        if isinstance(payload, Exception):
            raise payload
        return dict(payload)


def _caps_fixed(*, window=10_000, deployment="dep-1", route="route-1", routing=1):
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


def _run_staged(
    monkeypatch,
    llm,
    document_blocks: str,
    *,
    settings_kwargs=None,
    caps_fn=None,
):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import app.modules.analysis.service as svc_mod
    from app.ai.prompts.profile_extract import resolve_profile_prompt
    from app.core.config import Settings
    from app.modules.analysis.service import AnalysisService
    from app.modules.analysis.source_builder import DocumentSnapshot, PageSnapshot

    settings_args = dict(
        llm_base_url="https://gateway.example.test",
        llm_api_key="k",
        llm_model="qwen3-14b",
        analysis_llm_enable_thinking=False,
        analysis_token_count_max_attempts=3,
        analysis_project_split_max_depth=3,
        analysis_project_split_max_calls=8,
    )
    settings_args.update(settings_kwargs or {})
    settings = Settings(**settings_args)
    monkeypatch.setattr(
        svc_mod,
        "fetch_model_capabilities",
        caps_fn or (lambda *_a, **_k: _caps_fixed()),
    )
    monkeypatch.setattr(svc_mod, "get_settings", lambda: settings)

    doc_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    if "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in document_blocks:
        docs = (
            DocumentSnapshot(
                id=uuid.UUID(doc_id),
                original_filename="a.pdf",
                extension="pdf",
                mime_type="application/pdf",
                storage_key="k1",
                preview_storage_key=None,
                document_type_code="DOC-PROFILE",
                document_type_name="프로필",
                version_no=1,
                person_id=uuid.uuid4(),
                pages=[PageSnapshot(page_no=1, extracted_text="x" * 600, layout_json=None)],
            ),
            DocumentSnapshot(
                id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                original_filename="b.pdf",
                extension="pdf",
                mime_type="application/pdf",
                storage_key="k2",
                preview_storage_key=None,
                document_type_code="DOC-PROFILE",
                document_type_name="프로필",
                version_no=1,
                person_id=uuid.uuid4(),
                pages=[PageSnapshot(page_no=1, extracted_text="y" * 600, layout_json=None)],
            ),
        )
        allowed = {
            doc_id: {1},
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {1},
        }
        page_texts = {
            (doc_id, 1): "문서A DocA사업 복구프로젝트-1 P1 고객-1 2020-01 2021-12",
            ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1): (
                "문서B DocB사업 복구프로젝트-1 P2 고객-1 2020-01 2021-12"
            ),
        }
    else:
        docs = (
            DocumentSnapshot(
                id=uuid.UUID(doc_id),
                original_filename="a.pdf",
                extension="pdf",
                mime_type="application/pdf",
                storage_key="k1",
                preview_storage_key=None,
                document_type_code="DOC-PROFILE",
                document_type_name="프로필",
                version_no=1,
                person_id=uuid.uuid4(),
                pages=[
                    PageSnapshot(
                        page_no=1,
                        extracted_text=(
                            "경력 프로젝트 본문 복구프로젝트-1 P1 P2 DocA사업 "
                            "DocB사업 고객-1 2020-01 2021-12"
                        ),
                        layout_json=None,
                    )
                ],
            ),
        )
        allowed = {doc_id: {1, 2, 3}}
        page_texts = {
            (doc_id, 1): (
                "경력 프로젝트 본문 복구프로젝트-1 P1 P2 DocA사업 DocB사업 "
                "고객-1 2020-01 2021-12 AAA 문서A 문서B"
            ),
            (doc_id, 2): "문서A DocA사업 고객-1 2020-01 2021-12",
            (doc_id, 3): "문서B DocB사업 고객-1 2020-01 2021-12",
        }
    repo = MagicMock()
    repo.list_active_codes.return_value = [
        SimpleNamespace(
            code="JOB-MGT-PL", code_type="JOB", name="PL", is_active=True, parent_code=None
        ),
        SimpleNamespace(
            code="TECH-LANG-PYTHON",
            code_type="TECH",
            name="Python",
            is_active=True,
            parent_code=None,
        ),
        SimpleNamespace(
            code="EXP-MGT", code_type="EXP", name="사업관리", is_active=True, parent_code=None
        ),
    ]
    repo.list_aliases_for_codes.return_value = []

    service = AnalysisService.__new__(AnalysisService)
    service.repo = repo
    service.settings = settings
    service.llm = llm
    service.db = MagicMock()
    service.storage = MagicMock()

    catalog_map = {
        "JOB-MGT-PL": ("JOB", True),
        "TECH-LANG-PYTHON": ("TECH", True),
        "EXP-MGT": ("EXP", True),
    }
    prompt = resolve_profile_prompt("profile-extract-v20")
    return service._extract_staged_compact_candidate(
        prompt=prompt,
        document_blocks=document_blocks,
        catalog_map=catalog_map,
        allowed_documents=allowed,
        page_texts=page_texts,
        documents=docs,
        source_char_count=max(600, len(document_blocks)),
        base_log={"analysis_run_id": "test-run"},
        vlm_pages=0,
    )


def test_service_budget_fit_keeps_single_path_and_second_pass(monkeypatch) -> None:
    from app.ai.providers.token_count import TokenCountResult

    def count_fn(**kwargs):
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=500,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    llm = _BudgetLLM(
        count_fn=count_fn,
        project_payloads=[_projects_payload("P1"), _projects_payload("P2")],
    )
    blocks = _doc_block(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        alias_body="",
        pages="[PAGE 1]\n" + ("경력 프로젝트 본문 " * 40),
    )
    doc = _run_staged(monkeypatch, llm, blocks)
    phases = [c["phase"] for c in llm.chat_calls]
    assert phases[0] == "core"
    assert phases.count("projects") >= 2  # first + second-pass
    assert any(c.get("recovery_retry") is True for c in llm.chat_calls)
    # CORE must not receive an explicit completion cap.
    assert llm.chat_calls[0]["max_completion_tokens"] is None
    assert len(doc.projects) >= 1


def test_service_over_budget_splits_and_skips_full_second_pass(monkeypatch) -> None:
    from app.ai.providers.token_count import TokenCountResult

    def count_fn(*, user_prompt, **kwargs):
        # Combined two-doc prompt is over; single-doc fits.
        has_d1 = "D1" in user_prompt or "aaaaaaaa" in user_prompt
        has_d2 = "D2" in user_prompt or "bbbbbbbb" in user_prompt
        # After aliasing: D1/D2 markers.
        aliased_both = ("[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt)
        prompt_tokens = 9000 if aliased_both else 800
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=prompt_tokens,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    llm = _BudgetLLM(
        count_fn=count_fn,
        project_payloads=[
            _projects_payload("DocA사업"),
            _projects_payload("DocB사업"),
        ],
    )
    blocks = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서A " * 80),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서B " * 80),
        )
    )
    doc = _run_staged(monkeypatch, llm, blocks)
    project_chats = [c for c in llm.chat_calls if c["phase"] == "projects"]
    assert len(project_chats) >= 2
    # Adaptive path: no full-source second-pass recovery_retry on staged budget.
    assert not any(
        c.get("recovery_retry") is True and c.get("split_depth") in (None, 0)
        and "[DOCUMENT D1]" in (c.get("user_prompt") or "")
        and "[DOCUMENT D2]" in (c.get("user_prompt") or "")
        for c in project_chats
    )
    assert len(doc.projects) >= 1


def test_service_provider_503_does_not_fan_out_chunks(monkeypatch) -> None:
    import pytest
    from app.ai.providers.errors import AIProviderError
    from app.ai.providers.token_count import TokenCountResult
    from app.modules.analysis.service import InsufficientCandidateError

    state = {"n": 0}

    def count_fn(*, user_prompt, call_index, **kwargs):
        state["n"] += 1
        aliased_both = (
            "[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt
        )
        if aliased_both:
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=9000,
                context_window=10_000,
                status="VERIFIED",
                deployment_id="dep-1",
                route_id="route-1",
                routing_version=1,
            )
        # First per-chunk count fails as provider outage.
        raise AIProviderError("token-count HTTP 503")

    llm = _BudgetLLM(count_fn=count_fn)
    blocks = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서A " * 80),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서B " * 80),
        )
    )
    with pytest.raises(InsufficientCandidateError):
        _run_staged(monkeypatch, llm, blocks)
    # Must not chat PROJECT after provider outage; no page-level fan-out chats.
    assert not any(c["phase"] == "projects" for c in llm.chat_calls)


def test_service_length_retries_only_failed_chunk(monkeypatch) -> None:
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.ai.providers.token_count import TokenCountResult

    chat_i = {"n": 0}

    def count_fn(*, user_prompt, **kwargs):
        aliased_both = (
            "[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt
        )
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=9000 if aliased_both else 700,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    def chat_fn(*, log_context, user_prompt, **kwargs):
        phase = (log_context or {}).get("phase")
        if phase == "core":
            return _core_payload()
        chat_i["n"] += 1
        # First project chunk truncates once; subsequent succeed.
        if chat_i["n"] == 1:
            raise AIResponseTruncatedError(
                meta={"finish_reason": "length", "prompt_tokens": 700}
            )
        name = "DocA사업" if "문서A" in user_prompt else "DocB사업"
        return _projects_payload(name)

    llm = _BudgetLLM(count_fn=count_fn, chat_fn=chat_fn)
    blocks = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            alias_body="",
            pages="[PAGE 1]\nAAA\n\n[PAGE 2]\n" + ("문서A " * 80),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서B " * 80),
        )
    )
    doc = _run_staged(monkeypatch, llm, blocks)
    project_chats = [c for c in llm.chat_calls if c["phase"] == "projects"]
    assert any((c.get("split_depth") or 0) >= 1 for c in project_chats)
    assert len(doc.projects) >= 1


def test_service_persistent_route_drift_fail_closed(monkeypatch) -> None:
    import pytest
    from app.ai.providers.errors import AIProviderError
    from app.ai.providers.token_count import TokenCountResult
    from app.modules.analysis.service import InsufficientCandidateError

    # Capabilities keep advancing so every count mismatches.
    cap_state = {"v": 0}

    def caps_fn(*_a, **_k):
        cap_state["v"] += 1
        return _caps_fixed(routing=cap_state["v"])

    def count_fn(**kwargs):
        # Always report previous routing_version vs refreshed capability.
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=500,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=max(0, cap_state["v"] - 1),
        )

    llm = _BudgetLLM(count_fn=count_fn)
    blocks = _doc_block(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        alias_body="",
        pages="[PAGE 1]\n" + ("본문 " * 40),
    )
    with pytest.raises((InsufficientCandidateError, AIProviderError)):
        _run_staged(monkeypatch, llm, blocks, caps_fn=caps_fn)
    assert not any(c["phase"] == "projects" for c in llm.chat_calls)


def test_service_budget_fit_length_escalates_to_adaptive_not_recovery(monkeypatch) -> None:
    """Initial fit + first Chat length → adaptive split; no full-source recovery."""
    from app.ai.providers.errors import AIResponseTruncatedError
    from app.ai.providers.token_count import TokenCountResult

    project_chats = {"n": 0}

    def count_fn(*, user_prompt, **kwargs):
        aliased_both = (
            "[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt
        )
        # Root/combined always "fits" by token-count; length is the trigger.
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=900 if aliased_both else 700,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    def chat_fn(*, log_context, user_prompt, **kwargs):
        phase = (log_context or {}).get("phase")
        if phase == "core":
            return _core_payload()
        project_chats["n"] += 1
        # First PROJECT chat (full-source single path) truncates.
        if project_chats["n"] == 1:
            assert "[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt
            raise AIResponseTruncatedError(
                meta={"finish_reason": "length", "prompt_tokens": 900}
            )
        name = "DocA사업" if "문서A" in user_prompt else "DocB사업"
        return _projects_payload(name)

    llm = _BudgetLLM(count_fn=count_fn, chat_fn=chat_fn)
    blocks = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서A " * 80),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서B " * 80),
        )
    )
    doc = _run_staged(monkeypatch, llm, blocks)
    project_calls = [c for c in llm.chat_calls if c["phase"] == "projects"]
    assert project_chats["n"] >= 3  # 1 failed full + >=2 chunk successes
    assert not any(c.get("recovery_retry") is True for c in project_calls)
    assert any(
        ("[DOCUMENT D1]" in (c.get("user_prompt") or ""))
        and ("[DOCUMENT D2]" not in (c.get("user_prompt") or ""))
        for c in project_calls
    )
    assert len(doc.projects) >= 1


def test_service_budget_fit_prechat_overbudget_escalates_to_adaptive(
    monkeypatch,
) -> None:
    """Root count fits; pre-chat recount over-budget → adaptive; no stale Chat."""
    from app.ai.providers.token_count import TokenCountResult

    count_i = {"n": 0}

    def caps_fn(*_a, **_k):
        return _caps_fixed(window=10_000)

    def count_fn(*, user_prompt, **kwargs):
        count_i["n"] += 1
        aliased_both = (
            "[DOCUMENT D1]" in user_prompt and "[DOCUMENT D2]" in user_prompt
        )
        # First root count fits; subsequent full-source recounts over-budget
        # (simulates route/context change before Chat).
        if aliased_both and count_i["n"] > 1:
            return TokenCountResult(
                model="qwen3-14b",
                prompt_tokens=9000,
                context_window=10_000,
                status="VERIFIED",
                deployment_id="dep-1",
                route_id="route-1",
                routing_version=1,
            )
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=700 if not aliased_both else 800,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    llm = _BudgetLLM(
        count_fn=count_fn,
        project_payloads=[
            _projects_payload("DocA사업"),
            _projects_payload("DocB사업"),
        ],
    )
    blocks = (
        _doc_block(
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서A " * 80),
        )
        + _doc_block(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            alias_body="",
            pages="[PAGE 1]\n" + ("문서B " * 80),
        )
    )
    doc = _run_staged(monkeypatch, llm, blocks, caps_fn=caps_fn)
    project_calls = [c for c in llm.chat_calls if c["phase"] == "projects"]
    # Never chat the combined full-source prompt after over-budget recount.
    assert project_calls
    assert not any(
        "[DOCUMENT D1]" in (c.get("user_prompt") or "")
        and "[DOCUMENT D2]" in (c.get("user_prompt") or "")
        for c in project_calls
    )
    assert not any(c.get("recovery_retry") is True for c in project_calls)
    assert len(doc.projects) >= 1


def test_service_irreducible_overbudget_fail_closed(monkeypatch) -> None:
    import pytest
    from app.ai.providers.token_count import TokenCountResult
    from app.modules.analysis.service import InsufficientCandidateError

    def count_fn(**kwargs):
        # Always over-budget vs 10k window (matches capability identity).
        return TokenCountResult(
            model="qwen3-14b",
            prompt_tokens=9000,
            context_window=10_000,
            status="VERIFIED",
            deployment_id="dep-1",
            route_id="route-1",
            routing_version=1,
        )

    llm = _BudgetLLM(count_fn=count_fn)
    blocks = _doc_block(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        alias_body="",
        pages="[PAGE 1]\nSHORT",
    )
    with pytest.raises(InsufficientCandidateError):
        _run_staged(
            monkeypatch,
            llm,
            blocks,
            settings_kwargs={"analysis_project_split_max_depth": 1},
        )
    assert not any(c["phase"] == "projects" for c in llm.chat_calls)
