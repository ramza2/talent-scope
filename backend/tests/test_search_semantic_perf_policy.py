"""Small policy regressions for semantic performance routing."""

from __future__ import annotations

from typing import Any


def _fail_db():
    class FailOnExecuteDB:
        def execute(self, *args: Any, **kwargs: Any):  # pragma: no cover - failure path
            raise AssertionError("semantic exact short-circuit must not execute eligible COUNT")

    return FailOnExecuteDB()


def _patch_exact(monkeypatch, repo, sentinel):  # noqa: ANN001
    calls = {"exact": 0}

    def fake_exact(*, query_vector, eligible_subq, limit):  # noqa: ANN001
        calls["exact"] += 1
        assert query_vector == [1.0]
        assert eligible_subq is sentinel
        assert limit == 10
        return [], False

    monkeypatch.setattr(repo, "_semantic_channel_hits_exact", fake_exact)
    return calls


def test_force_exact_short_circuits_before_eligible_count(monkeypatch) -> None:
    """Required+semantic exact routing must not pay an eligible COUNT first."""
    from app.modules.search.query_repository import SearchQueryRepository

    repo = SearchQueryRepository(_fail_db())  # type: ignore[arg-type]
    sentinel = object()
    calls = _patch_exact(monkeypatch, repo, sentinel)

    hits, truncated = repo.semantic_channel_hits(
        query_vector=[1.0],
        eligible_subq=sentinel,
        limit=10,
        force_exact=True,
    )

    assert hits == []
    assert truncated is False
    assert calls["exact"] == 1


def test_production_exact_first_skips_eligible_count(monkeypatch) -> None:
    """ANN-disabled production routing must choose exact without a COUNT query."""
    from app.modules.search.query_repository import SearchQueryRepository

    repo = SearchQueryRepository(_fail_db())  # type: ignore[arg-type]
    sentinel = object()
    calls = _patch_exact(monkeypatch, repo, sentinel)

    hits, truncated = repo.semantic_channel_hits(
        query_vector=[1.0],
        eligible_subq=sentinel,
        limit=10,
        force_exact=False,
    )

    assert hits == []
    assert truncated is False
    assert calls["exact"] == 1


def test_production_semantic_ann_is_explicitly_disabled() -> None:
    """ANN remains benchmark-only until a measured crossover is approved."""
    from app.modules.search.ranking import (
        SEMANTIC_ANN_PRODUCTION_ENABLED,
        SEMANTIC_EXACT_ELIGIBLE_THRESHOLD,
    )

    assert SEMANTIC_ANN_PRODUCTION_ENABLED is False
    assert SEMANTIC_EXACT_ELIGIBLE_THRESHOLD >= 1_000_000
