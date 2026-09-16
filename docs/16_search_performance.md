# 16. Search Performance (EXPLAIN ANALYZE)

## Scope

This document records **measured** Hybrid Search performance work for TalentScope and the resulting production safety policy.

- Dataset: synthetic PERF DB only (`talentscope_perf`)
- Seed: `0x612d` (deterministic)
- Measured scale: people=2000, projects=6000, search_index_items≈28,590 (active current embeddings≈28,490)
- Crowd-out fixture: person0 with 500 near `DOCUMENT_CHUNK` vectors
- Ineligible-near flood: JOB-PL persons (~1/3) sit near the query axis (filtered ANN stress)
- Embedding in benchmark: synthetic 1024-d vectors (no live BGE-M3 HTTP)
- Measured environment: PostgreSQL 16.15, pgvector 0.6.0, pg_trgm 1.6

Harnesses:

- `backend/scripts/search_perf_benchmark.py` — seed / benchmark / recall / synthetic EXPLAIN
- `backend/scripts/search_perf_runtime_explain.py` — captures the **actual repository ANN SQL** at DBAPI execution time and replays that exact statement under `EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON)`

Both require `PERF_DATABASE_URL`. Seed/reset safety remains in `search_perf_benchmark.py`.

Artifacts from the existing 2k run:

- `/opt/cursor/artifacts/search-perf-before/`
- `/opt/cursor/artifacts/search-perf-after/`
- `/opt/cursor/artifacts/search-perf-recall-after.json`
- `/opt/cursor/artifacts/search-perf-channel-timing-after.json`

## Ranking / correctness invariants

Unchanged:

- Policy `rank-v2`
- RRF_K = 60
- Final weights: Required 0.20 / Retrieval 0.45 / Preferred 0.10 / Project 0.20 / Recency 0.05
- Project mix: Structured 0.60 / Keyword 0.20 / Semantic 0.20
- Person project: Best 0.70 / Count 0.20 / Duration 0.10 (caps 3 / 36)
- Hard filters remain DB SoT
- Person-best before channel LIMIT
- Current `embedding_model` + `embedding_version` only
- `source_weight` retained (`DOCUMENT_CHUNK` 0.7)
- Query embedding provider call remains once per request

## Production semantic selector

### Current production policy: exact-first

The measured 2k fixture did **not** establish an ANN performance crossover. On the latest same-fixture channel probe:

| Path | Median | p95 | `candidate_limit_reached` |
|---|---:|---:|---|
| Unfiltered router ANN (previous selector) | 158.5ms | 162.3ms | true |
| Unfiltered forced ANN | 142.3ms | 144.7ms | true |
| **Unfiltered exact** | **129.3ms** | **130.7ms** | true (limit+1) |
| Filtered `JOB-AI-DEV` exact | **76.4ms** | 80.3ms | true (limit+1) |
| Filtered forced ANN | 132.2ms | 140.0ms | true |

Therefore production keeps the exact person-best path until a larger-scale / real-distribution benchmark shows ANN is faster with acceptable recall. `SEMANTIC_EXACT_ELIGIBLE_THRESHOLD` is intentionally set far above the current expected scale. PERF/tests can set it to `0` to exercise ANN.

This avoids claiming a performance win that the measured fixture does not show.

### Required hard filters

`force_exact=True` short-circuits **before** the eligible-person `COUNT(*)`. Required+semantic searches therefore do not pay a count query only to choose the already-known exact path.

### Experimental ANN path

The typed-pool ANN implementation remains available for benchmarks and future large-scale activation:

1. Per `PROFILE` / `PROJECT` / `DOCUMENT_CHUNK` bounded KNN pool.
2. Current model/version + active embedding filters.
3. Eligible-person intersection.
4. `similarity * source_weight`.
5. Person-best collapse.
6. Final person LIMIT.

Because the pools are bounded, ANN continues to return `candidate_limit_reached=True` conservatively.

## Recall@K

Definition:

```text
Recall@K = |ExactTopK ∩ AnnTopK| / |ExactTopK|
```

Both exact and ANN sides are truncated to **K**. Top-K overlap is also recorded.

Existing corrected 2k measurement (forced ANN, seed `0x612d`):

| Scenario | eligible | Recall@10 | Recall@50 | Recall@100 | deep underfill |
|---|---:|---:|---:|---:|---|
| Unfiltered forced ANN | 2000 | 1.0 | 1.0 | 1.0 | no |
| Filtered `JOB-AI-DEV` forced ANN | 1334 | 1.0 | 1.0 | 1.0 | no |
| person_limit=500 / 2000 / 5000 | 2000 | 1.0 | 1.0 | 1.0 | no on this fixture |

These values are synthetic-fixture evidence only; they are not a production-scale guarantee.

## Production-shaped ANN EXPLAIN

Earlier `search_perf_benchmark.py` used a hand-written ANN SQL probe. That probe was useful for query-shape exploration, but it is not sufficient evidence that the exact repository SQL uses HNSW.

`search_perf_runtime_explain.py` closes that gap by:

1. forcing the repository ANN path only inside the PERF process,
2. capturing the SQLAlchemy statement and DBAPI parameters from the actual `_semantic_channel_hits_ann` execution,
3. replaying that exact statement as `EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON)`,
4. reporting whether `idx_search_index_embedding_hnsw` appears in the actual plan,
5. reporting requested pool size, ANN person count, Limit nodes, timing, PostgreSQL and extension versions.

Do **not** claim production HNSW usage until this runtime-shaped harness has been executed on the target PostgreSQL/pgvector environment and its artifact reviewed.

Example:

```bash
export PERF_DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_perf
cd backend
python scripts/search_perf_runtime_explain.py \
  --limit 500 \
  --iterations 5 \
  --out /opt/cursor/artifacts/search-perf-after/runtime-ann-explain.json
```

Repeat after seeding 2k / 5k / 10k (or the largest feasible scale) before lowering the production exact threshold.

## pgvector 0.6.0 note

The measured environment uses pgvector **0.6.0**. The ANN path must therefore be assessed with that version's filtering behavior; do not assume newer iterative-scan behavior. In particular, the global HNSW index plus `object_type`, model and version filters can affect actual pool rows and recall. Runtime EXPLAIN output must be checked for:

- HNSW Index Scan presence,
- filter placement,
- Actual Rows / Rows Removed by Filter,
- Limit-node actual rows versus requested pool,
- sort/window nodes,
- execution time.

No global PostgreSQL tuning or `ALTER SYSTEM` is introduced by this PR.

## Supporting optimizations retained

- Set-based `recent_project_date`
- Eligible metadata late-load after retrieval candidate merge
- Preferred match batching
- Stage timing log without query text / vectors / PII
- Existing keyword OR query (measured 2-stage UNION was slower)
- Project semantic remains bounded to candidate projects
- Evidence enrichment remains pagination-after only

## Rejected / deferred alternatives

| Alternative | Result / status |
|---|---|
| Global single ANN item pool | Rejected: `DOCUMENT_CHUNK` crowd-out risk |
| Keyword 2-stage FTS∪ILIKE∪trigram UNION | Rejected: ~29ms → ~117ms regression on measured fixture |
| ANN as production default at 2k | Rejected for now: exact was faster on latest same-fixture measurement |
| Filtered ANN | Deferred: exact is safer and faster on measured fixture |
| New partial HNSW / speculative B-tree/GIN | Not added; needs production-shaped evidence first |
| pgvector upgrade | Out of scope for this PR |

## Historical E2E measurement before final exact-first selector

The following numbers were captured while unfiltered semantic requests still selected ANN. They remain useful as historical evidence but are **not** current-selector performance claims.

| Scenario | Median ms | SQL count (median) | `candidate_limit_reached` |
|---|---:|---:|---|
| S1 no query | 35 | 17 | false |
| S2 structured | 101 | 30 | false |
| S3 keyword | 283 | 26 | true |
| S4 semantic | 439 | 27 | true (ANN at measurement time) |
| S5 keyword+semantic | 671 | 30 | true |
| S6 required+semantic | 201 | 27 | false (exact) |
| S7 hybrid full | 295 | 34 | false |
| S8 preferred+project | 819 | 44 | true |
| S9 deep page=10 | 1292 | 30 | true |
| S10 near max depth | 1217 | 29 | true |

A new E2E run is required after changing production selector policy; do not overwrite these values with estimates.

## Indexes / schema

No new index or schema migration is introduced.

Existing search indexes remain:

- `idx_search_index_embedding_hnsw`
- `idx_search_index_tsv`
- `idx_search_index_text_trgm`
- existing relation indexes for jobs / skills / expertise / project dimensions

## Acceptance before enabling ANN in production

Before lowering `SEMANTIC_EXACT_ELIGIBLE_THRESHOLD`, record at minimum:

- dataset size and SearchIndexItem count,
- exact median/p95,
- ANN median/p95,
- production-shaped HNSW plan,
- requested versus actual typed-pool rows,
- Recall@10 / Recall@50 / Recall@100,
- candidate underfill,
- pgvector version.

A production ANN selector should not be enabled merely because HNSW exists. It must show a meaningful latency win without unacceptable correctness/recall loss.

## Known limits

- Current measurements are synthetic, not real résumé/vector distribution.
- 5k/10k crossover is not yet established in committed evidence.
- HNSW is approximate.
- Cold/warm cache can materially change timing.
- Embedding HTTP latency is measured separately from DB retrieval.
- Actual planner choices can change as data grows.
- Re-measure on the deployment PostgreSQL/pgvector version.

## How to re-run

```bash
export PERF_DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_perf
export DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope
cd backend

python scripts/search_perf_benchmark.py --reset
python scripts/search_perf_benchmark.py --seed --people 2000 --seed-value 0x612d
python scripts/search_perf_benchmark.py --benchmark --out /opt/cursor/artifacts/search-perf-after --iterations 5
python scripts/search_perf_benchmark.py --recall
python scripts/search_perf_runtime_explain.py --limit 500 --iterations 5 \
  --out /opt/cursor/artifacts/search-perf-after/runtime-ann-explain.json
```

For crossover work, repeat the same measurement after reseeding at 5k / 10k or the largest practical local PERF scale. Only measured values belong in this document.
