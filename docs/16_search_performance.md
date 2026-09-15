# 16. Search Performance (EXPLAIN ANALYZE)

## Scope

This document records **measured** Hybrid Search performance work for TalentScope.

- Dataset: synthetic PERF DB only (`talentscope_perf`)
- Seed: `0x612d` (deterministic)
- Scale: people=2000, projects=6000, search_index_items≈28,590 (active current embeddings≈28,490)
- Crowd-out fixture: person0 with 500 near `DOCUMENT_CHUNK` vectors
- Ineligible-near flood: JOB-PL persons (~1/3) sit near the query axis (filtered ANN stress)
- Embedding in benchmark: synthetic 1024-d vectors (no live BGE-M3 HTTP)
- PostgreSQL 16.15, pgvector 0.6.0, pg_trgm 1.6

Harness: `backend/scripts/search_perf_benchmark.py`  
Requires `PERF_DATABASE_URL`. Refuses `--seed/--reset` when equal to `DATABASE_URL` or DB name lacks `_perf`/`_test`.

Artifacts:
- `/opt/cursor/artifacts/search-perf-before/`
- `/opt/cursor/artifacts/search-perf-after/`
- `/opt/cursor/artifacts/search-perf-recall-after.json`
- `/opt/cursor/artifacts/search-perf-channel-timing-after.json`

## Ranking / correctness invariants (unchanged)

- Policy `rank-v2`
- RRF_K = 60
- Final weights: Required 0.20 / Retrieval 0.45 / Preferred 0.10 / Project 0.20 / Recency 0.05
- Project mix: Structured 0.60 / Keyword 0.20 / Semantic 0.20
- Person project: Best 0.70 / Count 0.20 / Duration 0.10 (caps 3 / 36)
- Hard filters remain DB SoT (not post-filter override)
- Person-best before channel LIMIT (no DOCUMENT_CHUNK crowd-out)
- Current `embedding_model` + `embedding_version` only
- `source_weight` semantics retained (DOCUMENT_CHUNK 0.7)

## Semantic path policy (correctness)

1. **Typed-pool ANN (HNSW-friendly)** when no required hard filter and `eligible_count > 1000`  
   Per `PROFILE` / `PROJECT` / `DOCUMENT_CHUNK`: `ORDER BY embedding <=> q LIMIT pool`, union, score with `similarity * source_weight`, person-best, LIMIT.  
   Eligible hard-filter applied **after** HNSW pool (keeps KNN shape).

2. **Exact person-best when `eligible_count ≤ 1000`**  
   Small eligible sets stay on exact window (often cheaper than ANN).

3. **Required hard filter → `force_exact=True`**  
   If any required structured filter is present, semantic retrieval always uses the exact path.  
   Rationale (measured): filtered ANN can miss eligible near-neighbors that sit outside the global typed pools (ineligible-near flood). Exact also wins on latency for filtered sets in this fixture (see below).

4. **`candidate_limit_reached` on ANN (policy A)**  
   Typed ANN pools are bounded. On the ANN path the repository always returns `candidate_limit_reached=True` so the UI cannot silently claim full evaluation.  
   Required/filtered searches use exact and therefore do not inherit this ANN warning.

5. **Runtime embedding labels for PERF seed**  
   `_insert_index` resolves `MODEL`/`VERSION` at call time (not def-time defaults) so seed rows track active embedding settings.

## Recall@K definition (corrected)

```
Recall@K = |ExactTopK ∩ AnnTopK| / |ExactTopK|
```

Both sides are truncated to **K** (not “ExactTopK vs full AnnTopLimit”).

Top-K overlap is also recorded as `|ExactTopK ∩ AnnTopK| / |ExactTopK ∪ AnnTopK|`.

## Measured recall (seed `0x612d`, people=2000)

| Scenario | eligible | Recall@10 | Recall@50 | Recall@100 | deep underfill |
|---|---:|---:|---:|---:|---|
| Unfiltered forced ANN (limit=100) | 2000 | 1.0 | 1.0 | 1.0 | no |
| Filtered `JOB-AI-DEV` forced ANN | 1334 | 1.0 | 1.0 | 1.0 | no |
| Depth person_limit=500 / 2000 / 5000 | 2000 | 1.0 | 1.0 | 1.0 | no (ANN count matched exact within dataset) |

Production still uses **force_exact** for required filters even when forced-ANN recall is 1.0 here — latency + flood safety.

## Channel latency (repository probe, limit=`channel_candidate_limit(page=1)=500`)

| Path | Median | p95 | `candidate_limit_reached` |
|---|---:|---:|---|
| Unfiltered router (ANN) | 158.5ms | 162.3ms | true (ANN policy A) |
| Unfiltered forced ANN | 142.3ms | 144.7ms | true |
| Unfiltered exact | 129.3ms | 130.7ms | true (limit+1) |
| Filtered `JOB-AI-DEV` force_exact (policy) | **76.4ms** | 80.3ms | true (limit+1) |
| Filtered forced ANN (not production) | 132.2ms | 140.0ms | true |

Notes:
- Earlier ~45ms ANN figures were on a warmer/smaller-shape probe before the ineligible-near flood fixture; re-measure after distribution changes.
- Filtered exact remains faster than filtered ANN on this set — consistent with the force_exact policy.

## End-to-end scenarios (after policy, iterations=5, fake embed provider)

| Scenario | Median ms | SQL count (median) | `candidate_limit_reached` |
|---|---:|---:|---|
| S1 no query | 35 | 17 | false |
| S2 structured | 101 | 30 | false |
| S3 keyword | 283 | 26 | true |
| S4 semantic | 439 | 27 | true (ANN) |
| S5 keyword+semantic | 671 | 30 | true |
| S6 required+semantic | **201** | 27 | false (exact) |
| S7 hybrid full | 295 | 34 | false |
| S8 preferred+project | 819 | 44 | true |
| S9 deep page=10 | 1292 | 30 | true |
| S10 near max depth | 1217 | 29 | true |

SQL count stays bounded as page_size grows (no page_size-linear N+1).

## Chosen supporting optimizations (unchanged)

- Set-based `recent_project_date`
- Eligible metadata late-load after merge
- Preferred match batching
- Stage timing log (no PII / no query text / no vectors)

## Rejected alternatives (measured)

| Alternative | Result |
|---|---|
| Global single ANN item pool | DOCUMENT_CHUNK crowd-out risk; rejected vs typed pools |
| Keyword 2-stage FTS∪ILIKE∪`%` UNION then score | Keyword channel regressed (~29ms → ~117ms); reverted |
| Always-ANN for tiny eligible sets | Filtered exact faster; threshold=1000 retained |
| Filtered ANN without force_exact | Flood risk + slower than exact here; production uses force_exact |
| New partial HNSW / speculative B-tree/GIN | Not added; existing indexes reused |

## Indexes

**No new indexes** in this PR. Existing:

- `idx_search_index_embedding_hnsw` (HNSW cosine)
- `idx_search_index_tsv` / `idx_search_index_text_trgm`
- relation indexes for jobs/skills/expertise/etc.

## Known limits

- Synthetic distribution ≠ production résumé text/vector skew
- HNSW is approximate; monitor recall on real data (especially selective hard filters)
- ANN path always advertises `candidate_limit_reached` (policy A)
- Embedding HTTP latency is out of band (benchmark uses synthetic vectors)
- Re-measure after production data growth

## How to re-run

```bash
export PERF_DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_perf
export DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope
cd backend
python scripts/search_perf_benchmark.py --reset
# reset mutates DATABASE_URL via session factory — re-export before seed
export DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope
python scripts/search_perf_benchmark.py --seed --people 2000 --seed-value 0x612d
python scripts/search_perf_benchmark.py --explain --out /opt/cursor/artifacts/search-perf-after
python scripts/search_perf_benchmark.py --benchmark --out /opt/cursor/artifacts/search-perf-after --iterations 5
python scripts/search_perf_benchmark.py --recall
```
