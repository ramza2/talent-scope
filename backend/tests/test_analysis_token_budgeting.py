"""Adaptive ModelOps token budgeting + PROJECT chunk merge (focused)."""

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


def _caps(*, window: int, deployment: str = "dep-1", routing: int = 100):
    from app.ai.providers.model_capabilities import ModelCapabilities

    return ModelCapabilities(
        model_id="qwen3-14b",
        context_window=window,
        supports_thinking=True,
        supports_tools=True,
        deployment_id=deployment,
        routing_version=routing,
        status="VERIFIED",
    )


def test_budget_scales_with_context_window_no_hardcode() -> None:
    from app.modules.analysis.token_budget import compute_phase_token_budget

    b8 = compute_phase_token_budget(
        _caps(window=8192), output_ratio=0.35, safety_ratio=0.08
    )
    b16 = compute_phase_token_budget(
        _caps(window=16384), output_ratio=0.35, safety_ratio=0.08
    )
    assert b8.context_window == 8192
    assert b16.context_window == 16384
    assert b16.max_completion_tokens == b8.max_completion_tokens * 2
    assert b16.max_prompt_tokens == b8.max_prompt_tokens * 2
    # Guard: implementation must not embed a fixed window constant in ratios.
    assert b8.max_completion_tokens == int(8192 * 0.35)
    assert b16.max_completion_tokens == int(16384 * 0.35)


def test_no_literal_context_window_hardcode_in_budget_module() -> None:
    from pathlib import Path

    text = Path("app/modules/analysis/token_budget.py").read_text(encoding="utf-8")
    for banned in ("8192", "12288", "16384"):
        assert banned not in text


def test_fetch_capabilities_verified_ok(monkeypatch) -> None:
    import app.ai.providers.model_capabilities as caps_mod
    from app.core.config import Settings

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id": "qwen3-14b",
                "object": "model.capabilities",
                "status": "VERIFIED",
                "context_window": 8192,
                "supports_thinking": True,
                "supports_tools": True,
                "deployment_id": "dep-xyz",
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
            assert "/v1/models/" in url
            assert url.endswith("/capabilities")
            return _Resp()

    monkeypatch.setattr(caps_mod.httpx, "Client", _Client)
    settings = Settings(
        llm_base_url="https://gateway.example.test",
        llm_api_key="k",
        llm_model="qwen3-14b",
    )
    caps = caps_mod.fetch_model_capabilities(settings)
    assert caps.status == "VERIFIED"
    assert caps.context_window == 8192
    assert caps.deployment_id == "dep-xyz"
    assert caps.routing_version == 100


def test_fetch_capabilities_unknown_fail_closed(monkeypatch) -> None:
    import pytest
    import app.ai.providers.model_capabilities as caps_mod
    from app.ai.providers.errors import AIProviderError
    from app.core.config import Settings

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "UNKNOWN", "context_window": 8192}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(caps_mod.httpx, "Client", _Client)
    with pytest.raises(AIProviderError):
        caps_mod.fetch_model_capabilities(
            Settings(llm_base_url="https://g.test", llm_model="m", llm_api_key="k")
        )


def test_fetch_capabilities_null_window_fail_closed(monkeypatch) -> None:
    import pytest
    import app.ai.providers.model_capabilities as caps_mod
    from app.ai.providers.errors import AIProviderError
    from app.core.config import Settings

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "VERIFIED", "context_window": None}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(caps_mod.httpx, "Client", _Client)
    with pytest.raises(AIProviderError):
        caps_mod.fetch_model_capabilities(
            Settings(llm_base_url="https://g.test", llm_model="m", llm_api_key="k")
        )


def test_fetch_capabilities_http_503_fail_closed(monkeypatch) -> None:
    import pytest
    import app.ai.providers.model_capabilities as caps_mod
    from app.ai.providers.errors import AIProviderError
    from app.core.config import Settings

    class _Resp:
        status_code = 503

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(caps_mod.httpx, "Client", _Client)
    with pytest.raises(AIProviderError):
        caps_mod.fetch_model_capabilities(
            Settings(llm_base_url="https://g.test", llm_model="m", llm_api_key="k")
        )


def test_capabilities_route_changed_triggers_rebudget() -> None:
    from app.ai.providers.model_capabilities import capabilities_route_changed

    a = _caps(window=8192, deployment="d1", routing=1)
    b = _caps(window=8192, deployment="d2", routing=1)
    c = _caps(window=8192, deployment="d1", routing=2)
    d = _caps(window=16384, deployment="d1", routing=1)
    assert capabilities_route_changed(a, b) is True
    assert capabilities_route_changed(a, c) is True
    assert capabilities_route_changed(a, d) is True
    assert capabilities_route_changed(a, a) is False


def test_complete_json_sends_max_completion_tokens(monkeypatch) -> None:
    from app.ai.providers.llm import OpenAICompatibleLLMProvider
    from app.core.config import Settings
    import app.ai.providers.llm as llm_module

    observed: list[dict] = []

    def fake_post(*, payload, **kwargs):
        observed.append(dict(payload))
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true}'},
                }
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


def test_plan_single_chunk_when_budget_fits() -> None:
    from app.modules.analysis.token_budget import (
        compute_phase_token_budget,
        plan_project_source_chunks,
    )

    budget = compute_phase_token_budget(
        _caps(window=8192), output_ratio=0.35, safety_ratio=0.08
    )
    blocks = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\nshort\n"
    )

    def build_user_prompt(*, code_catalog, document_blocks, recovery_retry=False):
        return f"{code_catalog}\n{document_blocks}"

    planned = plan_project_source_chunks(
        blocks,
        system_prompt="sys",
        code_catalog="cat",
        build_user_prompt=build_user_prompt,
        budget=budget,
        chars_per_token=2.0,
    )
    assert planned == [blocks]


def test_plan_splits_by_document_when_over_budget() -> None:
    from app.modules.analysis.token_budget import (
        compute_phase_token_budget,
        plan_project_source_chunks,
    )

    # Tiny window forces split for multi-doc input.
    budget = compute_phase_token_budget(
        _caps(window=200), output_ratio=0.35, safety_ratio=0.08
    )
    d1 = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\n" + ("A" * 80) + "\n"
    )
    d2 = (
        "[DOCUMENT D2]\nfilename: b.pptx\ndocument_type: DOC-PROFILE\n"
        "content:\n[PAGE 1]\n" + ("B" * 80) + "\n"
    )
    blocks = d1 + d2

    def build_user_prompt(*, code_catalog, document_blocks, recovery_retry=False):
        return f"{code_catalog}\n{document_blocks}"

    planned = plan_project_source_chunks(
        blocks,
        system_prompt="s",
        code_catalog="c",
        build_user_prompt=build_user_prompt,
        budget=budget,
        chars_per_token=1.0,
    )
    assert len(planned) >= 2
    assert any("D1" in p for p in planned)
    assert any("D2" in p for p in planned)


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
    assert all("filename: a.pdf" in p for p in pages)
    huge = (
        "[DOCUMENT D1]\nfilename: a.pdf\ndocument_type: DOC-PROFILE\n"
        "content:\n" + ("X" * 1000)
    )
    parts = split_page_block_text(huge, max_body_chars=300)
    assert len(parts) >= 3
    assert all(p.startswith("[DOCUMENT D1]") for p in parts)


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
        source_refs=[
            SourceRef(document_id=doc, page_no=1, quote_text=name)
        ],
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
    assert len(forward.projects) == 2
    assert len(reverse.projects) == 2
    f_names = sorted(p.project_name for p in forward.projects)
    r_names = sorted(p.project_name for p in reverse.projects)
    assert f_names == r_names
    hw = next(p for p in forward.projects if p.project_name == "2017 HW1사업")
    hw_r = next(p for p in reverse.projects if p.project_name == "2017 HW1사업")
    assert {j.code for j in hw.jobs} == {"JOB-MGT", "JOB-OPS-SYS"}
    assert {j.code for j in hw_r.jobs} == {"JOB-MGT", "JOB-OPS-SYS"}
    assert {s.code for s in hw.skills} == {"TECH-SEC-AD"}
    assert {b.code for b in hw.business_domains} == {"BIZ-PUBLIC"}
    assert {r.document_id for r in hw.source_refs} == {_DOC_A, _DOC_B}
    # Serialize comparable view for order independence.
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


def test_merge_does_not_collapse_distinct_projects() -> None:
    from app.modules.analysis.compact_v8 import merge_project_chunk_candidates

    a = _proj("A사업", "고객A", "2018.01", "2018.12", doc=_DOC_A)
    b = _proj("B사업", "고객A", "2018.01", "2018.12", doc=_DOC_B)
    out = merge_project_chunk_candidates([_chunk_doc([a]), _chunk_doc([b])])
    assert len(out.projects) == 2


def test_length_finish_reason_still_raises_truncated(monkeypatch) -> None:
    """Authoritative length handling: do not parse truncated content."""
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
    """eed138c helper still first-wins on same-document match (no TECH merge)."""
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
