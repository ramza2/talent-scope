#!/usr/bin/env python3
"""Explain and time the *actual* repository semantic ANN SQL on PERF DB.

This companion harness intentionally does not hand-copy the ANN SQL. It captures
SQLAlchemy's DBAPI statement from ``SearchQueryRepository._semantic_channel_hits_ann``
and replays that exact statement under ``EXPLAIN (ANALYZE, BUFFERS, SETTINGS,
FORMAT JSON)``. Production currently stays on exact semantic retrieval until a
large-scale crossover is measured; this script keeps the ANN path measurable.

Safety: read-only, requires PERF_DATABASE_URL, never seeds or resets data.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import event, text

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _require_perf_url() -> str:
    url = os.environ.get("PERF_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("PERF_DATABASE_URL is required")
    return url


def _session_factory(database_url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url

    from app.core.config import get_settings

    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(database_url, pool_pre_ping=True)
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def _unit_vector(dim: int = 1024, index: int = 0) -> list[float]:
    out = [0.0] * dim
    out[index % dim] = 1.0
    return out


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[idx]


def _measure(fn, *, iterations: int) -> dict[str, float]:
    fn()  # warm-up
    elapsed: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        elapsed.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": round(statistics.median(elapsed), 3),
        "p95_ms": round(_percentile(elapsed, 0.95), 3),
        "min_ms": round(min(elapsed), 3),
        "max_ms": round(max(elapsed), 3),
    }


def _walk_plan(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    out.append(
        {
            "node_type": node.get("Node Type"),
            "index_name": node.get("Index Name"),
            "actual_rows": node.get("Actual Rows"),
            "actual_loops": node.get("Actual Loops"),
            "rows_removed_by_filter": node.get("Rows Removed by Filter"),
            "filter": node.get("Filter"),
            "sort_key": node.get("Sort Key"),
        }
    )
    for child in node.get("Plans") or []:
        _walk_plan(child, out)


def _explain_captured(engine, statement: str, parameters: Any) -> Any:
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute(
                "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) " + statement,
                parameters,
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()
            raw.rollback()
    finally:
        raw.close()


def run(*, limit: int, iterations: int, out: Path | None) -> dict[str, Any]:
    perf_url = _require_perf_url()
    SessionLocal, engine = _session_factory(perf_url)
    db = SessionLocal()
    try:
        from app.db.models.search import EMBEDDING_DIMENSIONS
        from app.modules.search import ranking as search_ranking
        from app.modules.search.query_repository import SearchQueryRepository
        from app.modules.search.query_schemas import SearchConditionBlock, SearchPeopleRequest
        from app.modules.search.ranking import semantic_ann_pool_size

        repo = SearchQueryRepository(db)
        request = SearchPeopleRequest(required=SearchConditionBlock(), page=1, page_size=20)
        expanded = repo.validate_and_expand_codes(request)
        eligible_subq = repo.eligible_person_ids_subquery(
            required=request.required,
            expanded=expanded,
            skill_match_mode=request.skill_match_mode,
        )
        query_vector = _unit_vector(int(EMBEDDING_DIMENSIONS))

        exact_timing = _measure(
            lambda: repo.semantic_channel_hits_exact(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
            ),
            iterations=iterations,
        )

        old_threshold = search_ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD
        search_ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD = 0
        captured: dict[str, Any] = {}

        def capture(
            conn, cursor, statement, parameters, context, executemany  # noqa: ANN001
        ) -> None:
            if "semantic_ann_pool" in statement and not statement.lstrip().upper().startswith("EXPLAIN"):
                captured["statement"] = statement
                captured["parameters"] = parameters

        event.listen(engine, "before_cursor_execute", capture)
        try:
            ann_timing = _measure(
                lambda: repo.semantic_channel_hits(
                    query_vector=query_vector,
                    eligible_subq=eligible_subq,
                    limit=limit,
                    force_exact=False,
                ),
                iterations=iterations,
            )
            ann_hits, ann_truncated = repo.semantic_channel_hits(
                query_vector=query_vector,
                eligible_subq=eligible_subq,
                limit=limit,
                force_exact=False,
            )
        finally:
            event.remove(engine, "before_cursor_execute", capture)
            search_ranking.SEMANTIC_EXACT_ELIGIBLE_THRESHOLD = old_threshold

        if "statement" not in captured:
            raise RuntimeError("Failed to capture actual semantic ANN statement")

        explain = _explain_captured(
            engine,
            captured["statement"],
            captured["parameters"],
        )
        plan_root = explain[0]["Plan"] if isinstance(explain, list) else explain["Plan"]
        nodes: list[dict[str, Any]] = []
        _walk_plan(plan_root, nodes)
        hnsw_nodes = [
            node
            for node in nodes
            if node.get("index_name") == "idx_search_index_embedding_hnsw"
        ]
        limit_nodes = [node for node in nodes if node.get("node_type") == "Limit"]

        versions = {
            "postgresql": db.execute(text("SHOW server_version")).scalar_one(),
            "pgvector": db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none(),
            "pg_trgm": db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
            ).scalar_one_or_none(),
        }
        eligible_count = int(
            db.execute(text("SELECT count(*) FROM person WHERE deleted_at IS NULL AND status='ACTIVE'"))
            .scalar_one()
        )

        report = {
            "source": "captured SearchQueryRepository runtime ANN statement",
            "versions": versions,
            "eligible_count": eligible_count,
            "person_limit": limit,
            "requested_pool_per_type": semantic_ann_pool_size(person_limit=limit),
            "exact_timing": exact_timing,
            "ann_timing": ann_timing,
            "ann_person_count": len(ann_hits),
            "ann_candidate_limit_reached": bool(ann_truncated),
            "hnsw_used": bool(hnsw_nodes),
            "hnsw_nodes": hnsw_nodes,
            "limit_nodes": limit_nodes,
            "planning_time_ms": explain[0].get("Planning Time") if isinstance(explain, list) else explain.get("Planning Time"),
            "execution_time_ms": explain[0].get("Execution Time") if isinstance(explain, list) else explain.get("Execution Time"),
            "plan": explain,
        }
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return report
    finally:
        db.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explain/timing for the actual TalentScope semantic ANN repository SQL"
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = run(limit=args.limit, iterations=args.iterations, out=args.out)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
