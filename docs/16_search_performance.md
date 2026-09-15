# 16. Search Performance (EXPLAIN ANALYZE)

## Scope

This document records **measured** Hybrid Search performance work for TalentScope.

- Dataset: synthetic PERF DB only (`talentscope_perf`)
- Seed: `0x612d` (deterministic)
- Scale: people=2000, projects=6000, search_index_items≈28,590 (active current embeddings≈28,490)
- Crowd-out fixture: person0 with 500 near `DOCUMENT_CHUNK` vectors
- Embedding in benchmark: synthetic 1024-d vectors (no live BGE-M3 HTTP)
- PostgreSQL 16.15, pgvector 0.6.0, pg_trgm 1.6

Harness: `backend/scripts/search_perf_benchmark.py`  
Requires `PERF_DATABASE_URL`. Refuses `--seed/--reset` when equal to `DATABASE_URL` or DB name lacks `_perf`/`_test`.

Artifacts:
- `/opt/cursor/artifacts/search-perf-before/`
- `/opt/cursor/artifacts/search-perf-after/`

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

## Chosen optimizations

1. **Semantic typed-pool ANN (HNSW-friendly)**  
   Per `PROFILE` / `PROJECT` / `DOCUMENT_CHUNK`: `ORDER BY embedding <=> q LIMIT pool`, union, score with `similarity * source_weight`, person-best, LIMIT.  
   Eligible hard-filter applied **after** HNSW pool (keeps KNN shape), then intersect eligible person ids.

2. **Exact fallback when eligible_count ≤ 1000**  
   Filtered searches (typical) use exact person-best window (often faster than ANN on small eligible sets).

3. **Set-based `recent_project_date`**  
   `GROUP BY person_id` derived table + `LEFT JOIN` instead of correlated scalar subquery.

4. **Eligible metadata late-load**  
   When keyword/semantic channels exist: channel SQL uses eligible subquery first; profile/recent metadata loaded only for merged candidate ids.

5. **Preferred match batching**  
   Page preferred code maps loaded with `IN (codes)` batch queries per category (not N queries per code).

6. **Stage timing log** (no PII / no query text / no vectors): eligible/keyword/embedding/semantic/preferred/project/enrichment/total ms.

## Rejected alternatives (measured)

| Alternative | Result |
|---|---|
| Global single ANN item pool | Faster raw KNN possible, but DOCUMENT_CHUNK crowd-out risk; rejected vs typed pools |
| Keyword 2-stage FTS∪ILIKE∪`%` UNION then score | On ~28k rows, median keyword channel **regressed** (~29ms → ~117ms); reverted to original OR + person-best |
| Always-ANN even for tiny eligible sets | Filtered eligible=334: exact ~28ms vs ANN ~124ms; hence exact threshold=1000 |
| New partial HNSW per object_type | Not needed after typed-pool query shape used existing `idx_search_index_embedding_hnsw` |
| Speculative new B-tree/GIN indexes | Not added; existing GIN/HNSW sufficient when query shape allows |

## EXPLAIN summary (people=2000)

| Query | Before plan | After plan | Notes |
|---|---|---|---|
| Semantic legacy window | Seq Scan + WindowAgg + Sort (~216ms exec) | still available as exact path | Full vector eval; **no HNSW** |
| Semantic typed ANN pools | — | **HNSW Index Scan** + Append + person WindowAgg (~45ms exec) | Uses `idx_search_index_embedding_hnsw` |
| Keyword OR FTS/ILIKE/trgm | Seq Scan + WindowAgg (~100–114ms exec on EXPLAIN) | unchanged shape | GIN not chosen at this scale; 2-stage UNION slower in app timings |
| Structured eligible | Nested Loop / index touches on person_job, grade, skills | set-based recent_project join | Existing relation indexes used |
| Project semantic (bounded ids) | WindowAgg over candidate projects | unchanged | Bounded brute-force acceptable |

## Channel latency (repository hybrid probe, same fixture)

| Stage | Before median | After median | Δ |
|---|---:|---:|---:|
| Eligible list | 7.98ms | 7.85ms | ~0 |
| Keyword | 28.94ms | 28.81ms | ~0 |
| Semantic | 93.87ms (p95 513.6ms) | **45.32ms (p95 60.0ms)** | **p95 ≫ improved** |

Exact-vs-ANN recall (person-level, limit=100): **Recall@10/50/100 = 1.0** on this synthetic set.

## End-to-end scenarios (after, iterations=5, fake embed provider)

| Scenario | Median ms | SQL count (median) |
|---|---:|---:|
| S1 no query | 33 | 17 |
| S2 structured | 97 | 30 |
| S3 keyword | 255 | 26 |
| S4 semantic | 283 | 27 |
| S5 keyword+semantic | 549 | 29 |
| S6 required+semantic | 190 | 27 |
| S7 hybrid full | 289 | 34 |
| S8 preferred+project | 615 | 44 |
| S9 deep page=10 | 978 | 29 |
| S10 near max depth | 912 | 28 |

SQL count stays bounded as page_size grows (no page_size-linear N+1).

## Indexes

**No new indexes** in this PR. Existing:

- `idx_search_index_embedding_hnsw` (HNSW cosine)
- `idx_search_index_tsv` / `idx_search_index_text_trgm`
- relation indexes for jobs/skills/expertise/etc.

## Known limits

- Synthetic distribution ≠ production résumé text/vector skew
- HNSW is approximate; monitor recall on real data
- Cold vs warm cache differs (see semantic p95 before)
- Embedding HTTP latency is out of band (benchmark uses synthetic vectors)
- Keyword GIN may appear at larger scales even though Seq Scan wins at 28k
- Re-measure after production data growth

## How to re-run

```bash
export PERF_DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_perf
export DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope
cd backend
python scripts/search_perf_benchmark.py --seed --people 2000
python scripts/search_perf_benchmark.py --explain --out /opt/cursor/artifacts/search-perf-after
python scripts/search_perf_benchmark.py --benchmark --out /opt/cursor/artifacts/search-perf-after --iterations 5
python scripts/search_perf_benchmark.py --recall
```
