"""Small policy regressions for semantic performance routing."""

from __future__ import annotations

from typing import Any


def test_force_exact_short_circuits_before_eligible_count(monkeypatch) -> None:
    """Required+semantic exact routing must not pay an eligible COUNT first."""
    from app.modules.search.query_repository import SearchQueryRepository

    class FailOnExecuteDB:
        def execute(self, *args: Any, **kwargs: Any):  # pragma: no cover - failure path
            raise AssertionError("force_exact must return before eligible COUNT/DB execute")

    repo = SearchQueryRepository(FailOnExecuteDB())  # type: ignore[arg-type]
    calls = {"exact": 0}

    def fake_exact(*, query_vector, eligible_subq, limit):  # noqa: ANN001
        calls["exact"] += 1
        assert query_vector == [1.0]
        assert eligible_subq is sentinel
        assert limit == 10
        return [], False

    sentinel = object()
    monkeypatch.setattr(repo, "_semantic_channel_hits_exact", fake_exact)

    hits, truncated = repo.semantic_channel_hits(
        query_vector=[1.0],
        eligible_subq=sentinel,
        limit=10,
        force_exact=True,
    )

    assert hits == []
    assert truncated is False
    assert calls["exact"] == 1


def test_production_semantic_threshold_is_exact_first() -> None:
    """ANN stays opt-in/benchmark-only until a measured crossover is approved."""
    from app.modules.search.ranking import SEMANTIC_EXACT_ELIGIBLE_THRESHOLD

    # This is intentionally far above the current candidate/search scale.
    # PERF tests can monkeypatch the threshold to 0 to exercise ANN.
    assert SEMANTIC_EXACT_ELIGIBLE_THRESHOLD >= 1_000_000
