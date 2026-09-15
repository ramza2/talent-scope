#!/usr/bin/env python3
"""Hybrid Search performance benchmark (PERF DB only).

Safety:
  - Requires PERF_DATABASE_URL
  - Refuses --seed/--reset when PERF_DATABASE_URL == DATABASE_URL
  - Refuses writes unless DB name contains _perf or _test

Usage:
  PERF_DATABASE_URL=postgresql+psycopg://talentscope:talentscope@127.0.0.1:5432/talentscope_perf \\
    python scripts/search_perf_benchmark.py --seed --people 2000
  PERF_DATABASE_URL=... python scripts/search_perf_benchmark.py --explain \\
    --out /opt/cursor/artifacts/search-perf-before
  PERF_DATABASE_URL=... python scripts/search_perf_benchmark.py --benchmark \\
    --out /opt/cursor/artifacts/search-perf-after --iterations 5
  PERF_DATABASE_URL=... python scripts/search_perf_benchmark.py --recall
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

SEED_DEFAULT = 0x612D
EMBED_DIM = 1024
# Fallbacks; overwritten from app when settings are available.
MODEL = "bge-m3"
VERSION = "embed-v1:c8000"

CODES: dict[str, list[tuple[str, str | None, str]]] = {
    "JOB": [
        ("JOB-AI-DEV", None, "AI Developer"),
        ("JOB-AI-ML", "JOB-AI-DEV", "ML Engineer"),
        ("JOB-PL", None, "Project Leader"),
    ],
    "TECH": [
        ("TECH-PYTHON", None, "Python"),
        ("TECH-JAVA", None, "Java"),
        ("TECH-SPRING", None, "Spring Boot"),
        ("TECH-POSTGRES", None, "PostgreSQL"),
        ("TECH-DOCKER", None, "Docker"),
    ],
    "EXP": [
        ("EXP-RAG", None, "RAG"),
        ("EXP-LLM", None, "LLM"),
    ],
    "BIZ": [
        ("BIZ-HEALTH", None, "Healthcare"),
        ("BIZ-FIN", None, "Finance"),
    ],
    "CUSTOMER_TYPE": [
        ("CUST-GOV", None, "Government"),
        ("CUST-ENT", None, "Enterprise"),
    ],
}

GRADES = ["BEGINNER", "INTERMEDIATE", "ADVANCED", "EXPERT"]


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def _db_name(url: str) -> str:
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql", 1))
    return (parsed.path or "/").lstrip("/")


def _require_perf_url(*, allow_write: bool) -> str:
    perf = os.environ.get("PERF_DATABASE_URL", "").strip()
    if not perf:
        raise SystemExit("PERF_DATABASE_URL is required")
    db = _db_name(perf)
    if allow_write:
        prod = os.environ.get("DATABASE_URL", "").strip()
        if prod and _normalize_url(perf) == _normalize_url(prod):
            raise SystemExit("Refusing --seed/--reset: PERF_DATABASE_URL == DATABASE_URL")
        if not any(tok in db.lower() for tok in ("_perf", "_test")):
            raise SystemExit(
                f"Refusing write on DB '{db}': name must contain _perf or _test"
            )
    return perf


def _resolve_embedding_constants() -> tuple[str, str, int]:
    global MODEL, VERSION, EMBED_DIM
    try:
        from app.db.models.search import EMBEDDING_DIMENSIONS
        from app.modules.search.embedding_policy import (
            current_embedding_model,
            effective_embedding_version,
        )

        MODEL = current_embedding_model() or MODEL
        VERSION = effective_embedding_version() or VERSION
        EMBED_DIM = int(EMBEDDING_DIMENSIONS)
    except Exception:  # noqa: BLE001 — keep hardcoded fallbacks for seed-only use
        pass
    return MODEL, VERSION, EMBED_DIM


def _session_factory(database_url: str):
    os.environ["DATABASE_URL"] = database_url
    from app.core.config import get_settings

    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(database_url, pool_pre_ping=True)
    _resolve_embedding_constants()
    return sessionmaker(bind=engine, autoflush=False, autocommit=False), engine


def _unit_vector(index: int, value: float = 1.0) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[index % EMBED_DIM] = float(value)
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _person_vector(rng: random.Random, person_idx: int) -> list[float]:
    v = [0.0] * EMBED_DIM
    v[0] = 0.15 + (person_idx % 50) * 0.001
    peak = 1 + (person_idx % (EMBED_DIM - 1))
    v[peak] = 0.85 + rng.random() * 0.1
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def _reset(engine) -> None:
    from sqlalchemy import text

    tables = [
        "search_index_item",
        "project_customer_type",
        "project_business_domain",
        "project_expertise",
        "project_skill",
        "project_job",
        "project",
        "person_expertise",
        "person_skill",
        "person_job",
        "certification",
        "person_profile",
        "person",
        "code_master",
    ]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"TRUNCATE TABLE {t} CASCADE"))


def _seed_codes(conn) -> None:
    from sqlalchemy import text

    rows: list[tuple[str, str, str | None, str]] = []
    for code_type, items in CODES.items():
        for code, parent, name in items:
            rows.append((code, code_type, parent, name))
    for code, code_type, parent, name in rows:
        if parent is None:
            conn.execute(
                text(
                    """
                    INSERT INTO code_master (code, code_type, parent_code, name, sort_order, is_active)
                    VALUES (:code, :ctype, NULL, :name, 0, true)
                    ON CONFLICT (code) DO NOTHING
                    """
                ),
                {"code": code, "ctype": code_type, "name": name},
            )
    for code, code_type, parent, name in rows:
        if parent is not None:
            conn.execute(
                text(
                    """
                    INSERT INTO code_master (code, code_type, parent_code, name, sort_order, is_active)
                    VALUES (:code, :ctype, :parent, :name, 0, true)
                    ON CONFLICT (code) DO NOTHING
                    """
                ),
                {"code": code, "ctype": code_type, "parent": parent, "name": name},
            )


def _resolved_index_labels(
    model: str | None = None,
    version: str | None = None,
) -> tuple[str, str]:
    """Resolve embedding labels at call time (avoids def-time default capture)."""
    return (
        MODEL if model is None else model,
        VERSION if version is None else version,
    )


def _insert_index(
    conn,
    *,
    person_id: uuid.UUID,
    object_type: str,
    object_id: uuid.UUID,
    search_text: str,
    source_weight: float,
    embedding: list[float],
    model: str | None = None,
    version: str | None = None,
) -> None:
    from sqlalchemy import text

    effective_model, effective_version = _resolved_index_labels(model, version)
    conn.execute(
        text(
            """
            INSERT INTO search_index_item (
              id, person_id, object_type, object_id, search_text,
              embedding, source_weight, embedding_model, embedding_version,
              is_active, indexed_at, created_at, updated_at, metadata_json
            ) VALUES (
              gen_random_uuid(), :pid, :otype, :oid, :stext,
              CAST(:emb AS vector), :sw, :model, :ver,
              true, now(), now(), now(), '{}'::jsonb
            )
            """
        ),
        {
            "pid": str(person_id),
            "otype": object_type,
            "oid": str(object_id),
            "stext": search_text,
            "emb": _vector_literal(embedding),
            "sw": source_weight,
            "model": effective_model,
            "ver": effective_version,
        },
    )


def _seed_person_batch(
    conn,
    *,
    rng: random.Random,
    seed: int,
    start_i: int,
    end_i: int,
    projects_per_person: int,
    chunks_per_person: int,
    crowd_out_chunks: int,
) -> None:
    from sqlalchemy import text

    query_axis = _unit_vector(0, 1.0)

    for i in range(start_i, end_i):
        pid = uuid.uuid5(uuid.NAMESPACE_DNS, f"perf-person-{seed}-{i}")
        grade = GRADES[i % len(GRADES)]
        career = 12 + (i % 240)
        name = f"Perf Person {i:05d}"

        conn.execute(
            text(
                """
                INSERT INTO person (id, status, created_at, updated_at)
                VALUES (:id, 'ACTIVE', now(), now())
                """
            ),
            {"id": str(pid)},
        )
        conn.execute(
            text(
                """
                INSERT INTO person_profile (
                  person_id, name, technical_grade,
                  career_confirmed_months, career_calculated_months,
                  profile_updated_at, updated_at
                ) VALUES (
                  :pid, :name, :grade, :career, :career,
                  now() - (:i || ' days')::interval, now()
                )
                """
            ),
            {"pid": str(pid), "name": name, "grade": grade, "career": career, "i": i % 400},
        )

        job = "JOB-AI-ML" if i % 3 == 0 else ("JOB-AI-DEV" if i % 3 == 1 else "JOB-PL")
        conn.execute(
            text(
                """
                INSERT INTO person_job (
                  id, person_id, job_code, job_type, source_type, is_active, sort_order, created_at
                ) VALUES (
                  gen_random_uuid(), :pid, :job, 'PRIMARY', 'AI_CONFIRMED', true, 0, now()
                )
                """
            ),
            {"pid": str(pid), "job": job},
        )

        techs = ["TECH-PYTHON"]
        if i % 2 == 0:
            techs.append("TECH-JAVA")
        if i % 5 == 0:
            techs.append("TECH-SPRING")
        if i % 7 == 0:
            techs.append("TECH-POSTGRES")
        if i % 11 == 0:
            techs.append("TECH-DOCKER")
        for tcode in techs:
            conn.execute(
                text(
                    """
                    INSERT INTO person_skill (
                      id, person_id, tech_code, source_type, is_representative, created_at
                    ) VALUES (
                      gen_random_uuid(), :pid, :tech, 'AI_CONFIRMED', false, now()
                    )
                    ON CONFLICT (person_id, tech_code) DO NOTHING
                    """
                ),
                {"pid": str(pid), "tech": tcode},
            )

        # Ensure EXPERT + EXP-RAG co-occur (structured filter needs both).
        exp = "EXP-RAG" if (i % 4 == 0 or grade == "EXPERT") else "EXP-LLM"
        conn.execute(
            text(
                """
                INSERT INTO person_expertise (
                  id, person_id, exp_code, evidence_type, source_type, created_at
                ) VALUES (
                  gen_random_uuid(), :pid, :exp, 'EXPLICIT', 'AI_CONFIRMED', now()
                )
                ON CONFLICT (person_id, exp_code) DO NOTHING
                """
            ),
            {"pid": str(pid), "exp": exp},
        )

        project_ids: list[uuid.UUID] = []
        for p in range(projects_per_person):
            prid = uuid.uuid5(uuid.NAMESPACE_DNS, f"perf-project-{seed}-{i}-{p}")
            project_ids.append(prid)
            start = date(2018, 1, 1) + timedelta(days=(i * 3 + p * 40) % 2000)
            end = start + timedelta(days=90 + (p * 30))
            pname = f"Project {i}-{p}"
            if p == 0 and i % 10 == 0:
                pname = f"DEMIS medical AI Spring Boot {i}"
            elif p == 0 and i % 17 == 0:
                pname = f"의료 AI RAG 플랫폼 {i}"
            conn.execute(
                text(
                    """
                    INSERT INTO project (
                      id, person_id, project_name, customer_name,
                      start_date, end_date, duration_months,
                      responsibilities, project_summary,
                      source_type, created_at, updated_at
                    ) VALUES (
                      :id, :pid, :pname, :cust,
                      :start, :end, :dur,
                      :resp, :summary,
                      'AI_CONFIRMED', now(), now()
                    )
                    """
                ),
                {
                    "id": str(prid),
                    "pid": str(pid),
                    "pname": pname,
                    "cust": "Gov Agency" if i % 3 == 0 else "Enterprise Co",
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "dur": 3 + p,
                    "resp": f"Built RAG pipeline with Python and Spring Boot for person {i}",
                    "summary": f"Summary DEMIS RAG LLM for {i}-{p}",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO project_job (project_id, job_code)
                    VALUES (:prid, :job) ON CONFLICT DO NOTHING
                    """
                ),
                {"prid": str(prid), "job": job},
            )
            for tcode in techs[:2]:
                conn.execute(
                    text(
                        """
                        INSERT INTO project_skill (project_id, tech_code)
                        VALUES (:prid, :tech) ON CONFLICT DO NOTHING
                        """
                    ),
                    {"prid": str(prid), "tech": tcode},
                )
            conn.execute(
                text(
                    """
                    INSERT INTO project_expertise (project_id, exp_code, evidence_type)
                    VALUES (:prid, :exp, 'EXPLICIT') ON CONFLICT DO NOTHING
                    """
                ),
                {"prid": str(prid), "exp": exp},
            )
            biz = "BIZ-HEALTH" if i % 2 == 0 else "BIZ-FIN"
            cust = "CUST-GOV" if i % 3 == 0 else "CUST-ENT"
            conn.execute(
                text(
                    """
                    INSERT INTO project_business_domain (project_id, biz_code)
                    VALUES (:prid, :biz) ON CONFLICT DO NOTHING
                    """
                ),
                {"prid": str(prid), "biz": biz},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO project_customer_type (project_id, customer_type_code)
                    VALUES (:prid, :cust) ON CONFLICT DO NOTHING
                    """
                ),
                {"prid": str(prid), "cust": cust},
            )

        # PROFILE embedding:
        # - person 1 near query axis (unfiltered top hit)
        # - JOB-PL persons (i%3==2) near axis → ineligible flood for JOB-AI-DEV filter
        # - remaining AI-job persons farther (_person_vector)
        if i == 1:
            pvec = _unit_vector(0, 0.97)
            profile_text = "Near query profile RAG specialist DEMIS"
        elif i % 3 == 2:
            # Ineligible-near flood for filtered ANN (JOB-PL not under JOB-AI-DEV tree)
            pvec = _unit_vector(0, 0.98 - (i % 100) * 0.0001)
            profile_text = f"Near-axis ineligible profile {i} DEMIS"
        else:
            pvec = _person_vector(rng, i)
            profile_text = f"Profile {i} Python RAG LLM expert DEMIS"
        _insert_index(
            conn,
            person_id=pid,
            object_type="PROFILE",
            object_id=pid,
            search_text=profile_text,
            source_weight=1.0,
            embedding=pvec,
        )
        if i % 20 == 0:
            _insert_index(
                conn,
                person_id=pid,
                object_type="PROFILE",
                object_id=pid,
                search_text=f"OLD profile {i}",
                source_weight=1.0,
                embedding=query_axis,
                model="old-model",
                version="old-v0",
            )

        for p, prid in enumerate(project_ids):
            # Person 2 first project near query axis
            if i == 2 and p == 0:
                emb = _unit_vector(0, 0.96)
                stext = "Near query project Spring Boot RAG 의료 AI"
            else:
                emb = _person_vector(rng, i * 10 + p + 3)
                stext = f"Project {i}-{p} Spring Boot RAG 의료 AI PostgreSQL"
            _insert_index(
                conn,
                person_id=pid,
                object_type="PROJECT",
                object_id=prid,
                search_text=stext,
                source_weight=1.0,
                embedding=emb,
            )

        n_chunks = crowd_out_chunks if i == 0 else chunks_per_person
        for c in range(n_chunks):
            if i == 0:
                emb = _unit_vector(0, 0.99 - (c % 50) * 0.0001)
            else:
                emb = _person_vector(rng, i * 100 + c + 7)
            _insert_index(
                conn,
                person_id=pid,
                object_type="DOCUMENT_CHUNK",
                object_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"perf-chunk-{seed}-{i}-{c}"),
                search_text=f"chunk {i}-{c} document content RAG DEMIS",
                source_weight=0.7,
                embedding=emb,
            )


def seed_dataset(
    *,
    people: int,
    projects_per_person: int,
    chunks_per_person: int,
    seed: int,
    crowd_out_chunks: int,
) -> dict[str, Any]:
    perf_url = _require_perf_url(allow_write=True)
    SessionLocal, engine = _session_factory(perf_url)
    del SessionLocal
    rng = random.Random(seed)
    from sqlalchemy import text

    _reset(engine)

    with engine.begin() as conn:
        _seed_codes(conn)

    batch = 200
    for start in range(0, people, batch):
        end = min(people, start + batch)
        with engine.begin() as conn:
            _seed_person_batch(
                conn,
                rng=rng,
                seed=seed,
                start_i=start,
                end_i=end,
                projects_per_person=projects_per_person,
                chunks_per_person=chunks_per_person,
                crowd_out_chunks=crowd_out_chunks,
            )

    with engine.begin() as conn:
        for tbl in (
            "person",
            "person_profile",
            "project",
            "search_index_item",
            "person_job",
            "person_skill",
            "person_expertise",
        ):
            conn.execute(text(f"ANALYZE {tbl}"))

    with engine.connect() as conn:
        stats = {
            "people": conn.execute(text("SELECT count(*) FROM person")).scalar_one(),
            "projects": conn.execute(text("SELECT count(*) FROM project")).scalar_one(),
            "search_index_items": conn.execute(
                text("SELECT count(*) FROM search_index_item")
            ).scalar_one(),
            "active_current_embeddings": conn.execute(
                text(
                    """
                    SELECT count(*) FROM search_index_item
                    WHERE is_active AND embedding IS NOT NULL
                      AND embedding_model = :m AND embedding_version = :v
                    """
                ),
                {"m": MODEL, "v": VERSION},
            ).scalar_one(),
            "seed": seed,
            "crowd_out_chunks": crowd_out_chunks,
            "embedding_model": MODEL,
            "embedding_version": VERSION,
            "embedding_dim": EMBED_DIM,
        }
        if int(stats["active_current_embeddings"]) <= 0:
            raise SystemExit(
                "Seed integrity failure: active_current_embeddings must be > 0 "
                f"(model={MODEL!r} version={VERSION!r})"
            )
        current_profiles = conn.execute(
            text(
                """
                SELECT count(*) FROM search_index_item
                WHERE is_active AND embedding IS NOT NULL
                  AND object_type = 'PROFILE'
                  AND embedding_model = :m AND embedding_version = :v
                """
            ),
            {"m": MODEL, "v": VERSION},
        ).scalar_one()
        if int(current_profiles) <= 0:
            raise SystemExit(
                "Seed integrity failure: no PROFILE rows with runtime labels "
                f"(model={MODEL!r} version={VERSION!r})"
            )
    engine.dispose()
    return stats


def _median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ordered = sorted(xs)
    idx = min(len(ordered) - 1, max(0, int(math.ceil(0.95 * len(ordered)) - 1)))
    return float(ordered[idx])


def _explain_json(db, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import text

    result = db.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) {sql}"),
        params,
    )
    row = result.scalar_one()
    if isinstance(row, str):
        return json.loads(row)[0]
    if isinstance(row, list):
        return row[0]
    return row


def _plan_summary(plan_root: dict[str, Any]) -> dict[str, Any]:
    plan = plan_root.get("Plan", plan_root)

    def walk(node: dict[str, Any], acc: dict[str, Any]) -> None:
        ntype = node.get("Node Type")
        if ntype:
            acc["node_types"].append(ntype)
            if "Index Name" in node:
                acc["indexes"].append(node["Index Name"])
            if "Index Cond" in node:
                acc["index_conds"].append(node["Index Cond"])
            rem = node.get("Rows Removed by Filter")
            if rem:
                acc["rows_removed_by_filter"] += int(rem)
            if ntype == "Sort":
                acc["sort_methods"].append(node.get("Sort Method"))
                acc["sort_spaces"].append(
                    {
                        "type": node.get("Sort Space Type"),
                        "used": node.get("Sort Space Used"),
                    }
                )
            shared_hit = node.get("Shared Hit Blocks")
            shared_read = node.get("Shared Read Blocks")
            if shared_hit is not None:
                acc["shared_hit_blocks"] += int(shared_hit)
            if shared_read is not None:
                acc["shared_read_blocks"] += int(shared_read)
        for child in node.get("Plans") or []:
            walk(child, acc)

    acc: dict[str, Any] = {
        "node_types": [],
        "indexes": [],
        "index_conds": [],
        "rows_removed_by_filter": 0,
        "sort_methods": [],
        "sort_spaces": [],
        "shared_hit_blocks": 0,
        "shared_read_blocks": 0,
    }
    walk(plan, acc)
    return {
        "planning_time_ms": plan_root.get("Planning Time"),
        "execution_time_ms": plan_root.get("Execution Time"),
        "actual_rows": plan.get("Actual Rows"),
        "shared_hit_blocks": acc["shared_hit_blocks"] or plan.get("Shared Hit Blocks"),
        "shared_read_blocks": acc["shared_read_blocks"] or plan.get("Shared Read Blocks"),
        "node_types": acc["node_types"],
        "indexes": sorted(set(acc["indexes"])),
        "index_conds": acc["index_conds"][:20],
        "rows_removed_by_filter": acc["rows_removed_by_filter"],
        "sort_methods": acc["sort_methods"],
        "sort_spaces": acc["sort_spaces"],
        "uses_hnsw": any("hnsw" in (i or "").lower() for i in acc["indexes"]),
        "uses_gin_tsv": any("tsv" in (i or "").lower() for i in acc["indexes"]),
        "uses_gin_trgm": any("trgm" in (i or "").lower() for i in acc["indexes"]),
        "has_windowagg": "WindowAgg" in acc["node_types"],
        "has_seq_scan": "Seq Scan" in acc["node_types"],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def run_explain(out_dir: Path) -> None:
    perf_url = _require_perf_url(allow_write=False)
    SessionLocal, engine = _session_factory(perf_url)
    out_dir.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import text

    db = SessionLocal()
    try:
        env = {
            "postgresql": db.execute(text("SHOW server_version")).scalar_one(),
            "pgvector": db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one(),
            "pg_trgm": db.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='pg_trgm'")
            ).scalar_one(),
            "db_name": _db_name(perf_url),
            "people": db.execute(text("SELECT count(*) FROM person")).scalar_one(),
            "search_index_items": db.execute(
                text("SELECT count(*) FROM search_index_item")
            ).scalar_one(),
            "embedding_model": MODEL,
            "embedding_version": VERSION,
        }
        _write_json(out_dir / "environment.json", env)

        qvec_lit = _vector_literal(_unit_vector(0, 1.0))

        structured_sql = """
        SELECT p.id
        FROM person p
        JOIN person_profile pp ON pp.person_id = p.id
        WHERE p.deleted_at IS NULL AND p.status = 'ACTIVE'
          AND pp.technical_grade = 'EXPERT'
          AND EXISTS (
            SELECT 1 FROM person_skill ps
            WHERE ps.person_id = p.id AND ps.tech_code = 'TECH-PYTHON'
          )
          AND EXISTS (
            SELECT 1 FROM person_expertise pe
            WHERE pe.person_id = p.id AND pe.exp_code = 'EXP-RAG'
          )
          AND (
            EXISTS (
              SELECT 1 FROM person_job pj
              WHERE pj.person_id = p.id AND pj.is_active
                AND pj.job_code IN ('JOB-AI-DEV', 'JOB-AI-ML')
            )
            OR EXISTS (
              SELECT 1 FROM project pr
              JOIN project_job prj ON prj.project_id = pr.id
              WHERE pr.person_id = p.id AND pr.deleted_at IS NULL
                AND prj.job_code IN ('JOB-AI-DEV', 'JOB-AI-ML')
            )
          )
        """
        structured_plan = _explain_json(db, structured_sql, {})
        _write_json(
            out_dir / "structured.json",
            {"summary": _plan_summary(structured_plan), "plan": structured_plan},
        )

        keyword_sql = """
        WITH ranked AS (
          SELECT si.id, si.person_id, si.source_weight,
                 CASE WHEN si.search_text ILIKE :pat THEN 1 ELSE 0 END AS exact_flag,
                 CASE WHEN si.search_tsv @@ websearch_to_tsquery('simple', :kw)
                      THEN ts_rank_cd(si.search_tsv, websearch_to_tsquery('simple', :kw))
                      ELSE 0 END AS fts_rank,
                 similarity(si.search_text, :kw) AS trigram_sim,
                 row_number() OVER (
                   PARTITION BY si.person_id
                   ORDER BY
                     CASE WHEN si.search_text ILIKE :pat THEN 1 ELSE 0 END DESC,
                     CASE WHEN si.search_tsv @@ websearch_to_tsquery('simple', :kw)
                          THEN ts_rank_cd(si.search_tsv, websearch_to_tsquery('simple', :kw))
                          ELSE 0 END DESC,
                     similarity(si.search_text, :kw) DESC,
                     si.source_weight DESC,
                     si.id ASC
                 ) AS rn
          FROM search_index_item si
          WHERE si.is_active
            AND (
              si.search_tsv @@ websearch_to_tsquery('simple', :kw)
              OR si.search_text ILIKE :pat
              OR similarity(si.search_text, :kw) >= 0.25
            )
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY exact_flag DESC, fts_rank DESC, trigram_sim DESC, source_weight DESC, id ASC
        LIMIT 501
        """
        keyword_summaries: dict[str, Any] = {}
        for label, kw in [
            ("common_rag", "RAG"),
            ("rare_demis", "DEMIS"),
            ("spring_boot", "Spring Boot"),
            ("korean", "의료 AI"),
        ]:
            plan = _explain_json(db, keyword_sql, {"kw": kw, "pat": f"%{kw}%"})
            payload = {
                "keyword_label": label,
                "keyword": kw,
                "summary": _plan_summary(plan),
                "plan": plan,
            }
            _write_json(out_dir / f"keyword_{label}.json", payload)
            keyword_summaries[label] = payload["summary"]
        _write_json(
            out_dir / "keyword.json",
            {
                "primary": "rare_demis",
                "summaries": keyword_summaries,
                "detail_file": "keyword_rare_demis.json",
            },
        )

        semantic_legacy_sql = """
        WITH ranked AS (
          SELECT si.id, si.person_id, si.object_type, si.object_id, si.source_weight,
                 (si.embedding <=> CAST(:qvec AS vector)) AS distance,
                 GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                   * si.source_weight::float AS effective_score,
                 row_number() OVER (
                   PARTITION BY si.person_id
                   ORDER BY
                     GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                       * si.source_weight::float DESC,
                     si.id ASC
                 ) AS rn
          FROM search_index_item si
          WHERE si.is_active
            AND si.embedding IS NOT NULL
            AND si.embedding_model = :model
            AND si.embedding_version = :version
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY effective_score DESC, id ASC
        LIMIT 501
        """
        sem_legacy = _explain_json(
            db,
            semantic_legacy_sql,
            {"qvec": qvec_lit, "model": MODEL, "version": VERSION},
        )
        _write_json(
            out_dir / "semantic_legacy_window.json",
            {"summary": _plan_summary(sem_legacy), "plan": sem_legacy},
        )

        semantic_ann_sql = """
        WITH pool AS (
          (
            SELECT si.id, si.person_id, si.object_type, si.object_id, si.source_weight,
                   (si.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM search_index_item si
            WHERE si.is_active AND si.embedding IS NOT NULL
              AND si.embedding_model = :model AND si.embedding_version = :version
              AND si.object_type = 'PROFILE'
            ORDER BY si.embedding <=> CAST(:qvec AS vector)
            LIMIT :pool
          )
          UNION ALL
          (
            SELECT si.id, si.person_id, si.object_type, si.object_id, si.source_weight,
                   (si.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM search_index_item si
            WHERE si.is_active AND si.embedding IS NOT NULL
              AND si.embedding_model = :model AND si.embedding_version = :version
              AND si.object_type = 'PROJECT'
            ORDER BY si.embedding <=> CAST(:qvec AS vector)
            LIMIT :pool
          )
          UNION ALL
          (
            SELECT si.id, si.person_id, si.object_type, si.object_id, si.source_weight,
                   (si.embedding <=> CAST(:qvec AS vector)) AS distance
            FROM search_index_item si
            WHERE si.is_active AND si.embedding IS NOT NULL
              AND si.embedding_model = :model AND si.embedding_version = :version
              AND si.object_type = 'DOCUMENT_CHUNK'
            ORDER BY si.embedding <=> CAST(:qvec AS vector)
            LIMIT :pool
          )
        ),
        scored AS (
          SELECT id, person_id, object_type, object_id, source_weight, distance,
                 GREATEST(0.0, 1.0 - distance) * source_weight::float AS effective_score,
                 row_number() OVER (
                   PARTITION BY person_id
                   ORDER BY GREATEST(0.0, 1.0 - distance) * source_weight::float DESC, id ASC
                 ) AS rn
          FROM pool
        )
        SELECT * FROM scored WHERE rn = 1
        ORDER BY effective_score DESC, id ASC
        LIMIT 501
        """
        sem_ann = _explain_json(
            db,
            semantic_ann_sql,
            {"qvec": qvec_lit, "model": MODEL, "version": VERSION, "pool": 4000},
        )
        _write_json(
            out_dir / "semantic_ann_typed_pools.json",
            {"summary": _plan_summary(sem_ann), "plan": sem_ann},
        )
        _write_json(
            out_dir / "semantic.json",
            {
                "legacy": _plan_summary(sem_legacy),
                "ann_typed_pools": _plan_summary(sem_ann),
            },
        )

        project_ids = [
            str(r[0]) for r in db.execute(text("SELECT id FROM project LIMIT 500")).all()
        ]
        if project_ids:
            project_sql = """
            WITH ranked AS (
              SELECT si.object_id AS project_id,
                     GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                       * si.source_weight::float AS score,
                     row_number() OVER (
                       PARTITION BY si.object_id
                       ORDER BY GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                         * si.source_weight::float DESC, si.id ASC
                     ) AS rn
              FROM search_index_item si
              WHERE si.is_active AND si.object_type = 'PROJECT'
                AND si.object_id = ANY(CAST(:pids AS uuid[]))
                AND si.embedding IS NOT NULL
                AND si.embedding_model = :model AND si.embedding_version = :version
            )
            SELECT project_id, score FROM ranked WHERE rn = 1
            """
            proj_plan = _explain_json(
                db,
                project_sql,
                {
                    "qvec": qvec_lit,
                    "pids": project_ids,
                    "model": MODEL,
                    "version": VERSION,
                },
            )
            _write_json(
                out_dir / "project-ranking.json",
                {
                    "summary": _plan_summary(proj_plan),
                    "project_sample_size": len(project_ids),
                    "plan": proj_plan,
                },
            )

        evidence_sql = """
        SELECT el.target_type, el.target_id, el.evidence_id
        FROM evidence_link el
        WHERE el.target_type = 'PROJECT'
          AND el.target_id IN (SELECT id FROM project LIMIT 20)
        """
        try:
            ev_plan = _explain_json(db, evidence_sql, {})
            _write_json(
                out_dir / "evidence-enrichment.json",
                {"summary": _plan_summary(ev_plan), "plan": ev_plan},
            )
        except Exception as exc:  # noqa: BLE001
            _write_json(
                out_dir / "evidence-enrichment.json",
                {"note": "evidence tables empty or query failed", "error": str(exc)},
            )

        from app.modules.search.query_repository import SearchQueryRepository
        from app.modules.search.query_schemas import (
            GradeFilter,
            SearchConditionBlock,
            SearchPeopleRequest,
        )
        from app.modules.search.ranking import channel_candidate_limit

        repo = SearchQueryRepository(db)
        req = SearchPeopleRequest(
            required=SearchConditionBlock(
                grade=GradeFilter(values=["EXPERT"]),
                skills=["TECH-PYTHON"],
                expertise=["EXP-RAG"],
                jobs=["JOB-AI-DEV"],
            ),
            keyword_query="RAG",
            semantic_query="RAG LLM specialist",
            page=1,
            page_size=20,
        )
        expanded = repo.validate_and_expand_codes(req)
        eligible_subq = repo.eligible_person_ids_subquery(
            required=req.required,
            expanded=expanded,
            skill_match_mode=req.skill_match_mode,
        )
        limit = channel_candidate_limit(page=req.page, page_size=req.page_size)
        qvec = _unit_vector(0, 1.0)

        times: dict[str, list[float]] = {"eligible": [], "keyword": [], "semantic": []}
        rows: list[Any] = []
        kh: list[Any] = []
        sh: list[Any] = []
        _ = repo.list_eligible_persons(
            required=req.required, expanded=expanded, skill_match_mode=req.skill_match_mode
        )
        for _ in range(5):
            t0 = time.perf_counter()
            rows = repo.list_eligible_persons(
                required=req.required,
                expanded=expanded,
                skill_match_mode=req.skill_match_mode,
            )
            times["eligible"].append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            kh, _ = repo.keyword_channel_hits(
                keyword="RAG", eligible_subq=eligible_subq, limit=limit
            )
            times["keyword"].append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            sh, _ = repo.semantic_channel_hits(
                query_vector=qvec, eligible_subq=eligible_subq, limit=limit
            )
            times["semantic"].append((time.perf_counter() - t0) * 1000)

        hybrid = {
            "eligible_median_ms": _median(times["eligible"]),
            "eligible_p95_ms": _p95(times["eligible"]),
            "keyword_median_ms": _median(times["keyword"]),
            "keyword_p95_ms": _p95(times["keyword"]),
            "semantic_median_ms": _median(times["semantic"]),
            "semantic_p95_ms": _p95(times["semantic"]),
            "eligible_count": len(rows),
            "keyword_hits": len(kh),
            "semantic_hits": len(sh),
            "channel_limit": limit,
        }
        _write_json(out_dir / "hybrid.json", hybrid)
        print(json.dumps({"explain_out": str(out_dir), "hybrid": hybrid, "env": env}, indent=2))
    finally:
        db.close()
        engine.dispose()


def run_benchmark(out_dir: Path, *, iterations: int = 5) -> None:
    perf_url = _require_perf_url(allow_write=False)
    SessionLocal, engine = _session_factory(perf_url)
    out_dir.mkdir(parents=True, exist_ok=True)
    from unittest.mock import patch

    from sqlalchemy import event

    from app.modules.search.query_schemas import (
        GradeFilter,
        PreferredConditionBlock,
        SearchConditionBlock,
        SearchPeopleRequest,
    )
    from app.modules.search.query_service import SearchQueryService

    scenarios: list[tuple[str, SearchPeopleRequest]] = [
        ("S1_no_query", SearchPeopleRequest(page=1, page_size=20)),
        (
            "S2_structured",
            SearchPeopleRequest(
                required=SearchConditionBlock(
                    grade=GradeFilter(values=["EXPERT"]),
                    skills=["TECH-PYTHON"],
                    expertise=["EXP-RAG"],
                    jobs=["JOB-AI-DEV"],
                ),
                page=1,
                page_size=20,
            ),
        ),
        ("S3_keyword", SearchPeopleRequest(keyword_query="DEMIS", page=1, page_size=20)),
        (
            "S4_semantic",
            SearchPeopleRequest(semantic_query="RAG specialist", page=1, page_size=20),
        ),
        (
            "S5_keyword_semantic",
            SearchPeopleRequest(
                keyword_query="RAG", semantic_query="RAG specialist", page=1, page_size=20
            ),
        ),
        (
            "S6_required_semantic",
            SearchPeopleRequest(
                required=SearchConditionBlock(grade=GradeFilter(values=["EXPERT"])),
                semantic_query="RAG specialist",
                page=1,
                page_size=20,
            ),
        ),
        (
            "S7_hybrid_full",
            SearchPeopleRequest(
                required=SearchConditionBlock(
                    grade=GradeFilter(values=["EXPERT"]),
                    skills=["TECH-PYTHON"],
                    jobs=["JOB-AI-DEV"],
                ),
                keyword_query="Spring Boot",
                semantic_query="RAG LLM",
                page=1,
                page_size=20,
            ),
        ),
        (
            "S8_preferred_project",
            SearchPeopleRequest(
                preferred=PreferredConditionBlock(
                    skills=["TECH-PYTHON", "TECH-JAVA", "TECH-SPRING"],
                    expertise=["EXP-RAG"],
                    business_domains=["BIZ-HEALTH"],
                ),
                keyword_query="의료 AI",
                semantic_query="healthcare RAG",
                page=1,
                page_size=20,
            ),
        ),
        (
            "S9_deep_page",
            SearchPeopleRequest(
                keyword_query="RAG", semantic_query="RAG", page=10, page_size=20
            ),
        ),
        (
            "S10_near_max_depth",
            SearchPeopleRequest(
                keyword_query="Python",
                semantic_query="Python engineer",
                page=25,
                page_size=20,
            ),
        ),
    ]

    qvec = _unit_vector(0, 1.0)

    class _FakeEmbed:
        def embed_text(self, text: str) -> list[float]:
            return list(qvec)

    results: dict[str, Any] = {}
    db = SessionLocal()
    try:
        with patch(
            "app.ai.providers.embedding.get_embedding_provider",
            return_value=_FakeEmbed(),
        ):
            with patch.dict(os.environ, {"EMBEDDING_ENABLED": "true"}, clear=False):
                from app.core.config import get_settings

                get_settings.cache_clear()
                svc = SearchQueryService(db)
                for name, req in scenarios:
                    try:
                        svc.search_people(req)
                    except Exception as exc:  # noqa: BLE001
                        results[name] = {"error": str(exc)}
                        continue
                    samples: list[float] = []
                    sql_counts: list[int] = []
                    last_meta = None
                    resp = None
                    for _ in range(iterations):
                        counter = {"n": 0}

                        def before_cursor(
                            conn, cursor, statement, parameters, context, executemany
                        ):  # noqa: ANN001
                            counter["n"] += 1

                        event.listen(engine, "before_cursor_execute", before_cursor)
                        t0 = time.perf_counter()
                        resp = svc.search_people(req)
                        elapsed = (time.perf_counter() - t0) * 1000
                        event.remove(engine, "before_cursor_execute", before_cursor)
                        samples.append(elapsed)
                        sql_counts.append(counter["n"])
                        last_meta = resp.meta.model_dump()
                    results[name] = {
                        "median_ms": _median(samples),
                        "p95_ms": _p95(samples),
                        "max_ms": max(samples) if samples else 0,
                        "sql_count_median": _median([float(x) for x in sql_counts]),
                        "meta": last_meta,
                        "result_count": len(resp.data) if resp is not None else 0,
                    }
        _write_json(out_dir / "benchmark_scenarios.json", results)
        print(json.dumps(results, indent=2, default=str))
    finally:
        db.close()
        engine.dispose()


def _exact_semantic_person_ids(
    db,
    *,
    query_vector: list[float],
    limit: int,
) -> list[uuid.UUID]:
    """Exact person-best semantic ranking via window-over-all (raw SQL)."""
    from sqlalchemy import text

    qvec_lit = _vector_literal(query_vector)
    rows = db.execute(
        text(
            """
            WITH ranked AS (
              SELECT si.id, si.person_id,
                     GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                       * si.source_weight::float AS effective_score,
                     row_number() OVER (
                       PARTITION BY si.person_id
                       ORDER BY
                         GREATEST(0.0, 1.0 - (si.embedding <=> CAST(:qvec AS vector)))
                           * si.source_weight::float DESC,
                         si.id ASC
                     ) AS rn
              FROM search_index_item si
              JOIN person p ON p.id = si.person_id
              WHERE si.is_active
                AND si.embedding IS NOT NULL
                AND si.embedding_model = :model
                AND si.embedding_version = :version
                AND p.deleted_at IS NULL
                AND p.status = 'ACTIVE'
            )
            SELECT person_id FROM ranked WHERE rn = 1
            ORDER BY effective_score DESC, id ASC
            LIMIT :lim
            """
        ),
        {"qvec": qvec_lit, "model": MODEL, "version": VERSION, "lim": limit},
    ).all()
    return [uuid.UUID(str(r[0])) for r in rows]


def run_recall(*, limit: int = 100) -> None:
    """Compare exact vs ANN person lists with true top-K Recall@K.

    Recall@K = |ExactTopK ∩ AnnTopK| / |ExactTopK|
    (ANN side is also truncated to K — not the full ANN limit window.)
    """
    from sqlalchemy import func, select

    perf_url = _require_perf_url(allow_write=False)
    SessionLocal, engine = _session_factory(perf_url)
    db = SessionLocal()
    try:
        from app.modules.search.query_repository import SearchQueryRepository
        from app.modules.search.query_schemas import (
            SearchConditionBlock,
            SearchPeopleRequest,
        )
        from app.modules.search.ranking import (
            SEMANTIC_EXACT_ELIGIBLE_THRESHOLD,
            ChannelHit,
            semantic_ann_pool_size,
        )

        repo = SearchQueryRepository(db)
        qvec = _unit_vector(0, 1.0)

        def _ids(hits: list) -> list:
            if hits and isinstance(hits[0], ChannelHit):
                return [h.person_id for h in hits]
            return list(hits)

        def _recall_at(exact_ids: list, ann_ids: list, k: int) -> float:
            if not exact_ids:
                return 1.0
            top_exact = exact_ids[:k]
            if not top_exact:
                return 1.0
            ann_top = set(ann_ids[:k])
            return sum(1 for pid in top_exact if pid in ann_top) / len(top_exact)

        def _overlap_at(exact_ids: list, ann_ids: list, k: int) -> float:
            a = set(exact_ids[:k])
            b = set(ann_ids[:k])
            if not a and not b:
                return 1.0
            return len(a & b) / max(len(a | b), 1)

        def _run_pair(*, req: SearchPeopleRequest, person_limit: int, force_ann: bool):
            expanded = repo.validate_and_expand_codes(req)
            eligible_subq = repo.eligible_person_ids_subquery(
                required=req.required,
                expanded=expanded,
                skill_match_mode=req.skill_match_mode,
            )
            eligible_count = int(
                db.execute(
                    select(func.count()).select_from(eligible_subq)
                ).scalar_one()
                or 0
            )
            exact_hits, exact_trunc = repo.semantic_channel_hits_exact(
                query_vector=qvec, eligible_subq=eligible_subq, limit=person_limit
            )
            if force_ann:
                ann_hits, ann_trunc = repo._semantic_channel_hits_ann(
                    query_vector=qvec, eligible_subq=eligible_subq, limit=person_limit
                )
                path = "forced_ann"
            else:
                # Mirror router without service-level force_exact.
                ann_hits, ann_trunc = repo.semantic_channel_hits(
                    query_vector=qvec,
                    eligible_subq=eligible_subq,
                    limit=person_limit,
                    force_exact=False,
                )
                path = (
                    "exact_threshold"
                    if eligible_count <= SEMANTIC_EXACT_ELIGIBLE_THRESHOLD
                    else "ann"
                )
            exact_ids = _ids(exact_hits)
            ann_ids = _ids(ann_hits)
            pool = semantic_ann_pool_size(person_limit=person_limit)
            underfill = len(exact_ids) >= min(person_limit, eligible_count) and len(
                ann_ids
            ) < min(person_limit, len(exact_ids))
            return {
                "path": path,
                "eligible_count": eligible_count,
                "person_limit": person_limit,
                "pool_size_per_type": pool,
                "exact_count": len(exact_ids),
                "ann_count": len(ann_ids),
                "exact_truncated": bool(exact_trunc),
                "ann_candidate_limit_reached": bool(ann_trunc),
                "deep_page_underfill": bool(underfill),
                "recall_at_10": _recall_at(exact_ids, ann_ids, 10),
                "recall_at_50": _recall_at(exact_ids, ann_ids, 50),
                "recall_at_100": _recall_at(exact_ids, ann_ids, 100),
                "recall_at_limit": _recall_at(exact_ids, ann_ids, person_limit),
                "overlap_at_10": _overlap_at(exact_ids, ann_ids, 10),
                "overlap_at_50": _overlap_at(exact_ids, ann_ids, 50),
                "overlap_at_100": _overlap_at(exact_ids, ann_ids, 100),
            }

        unfiltered = _run_pair(
            req=SearchPeopleRequest(
                required=SearchConditionBlock(), page=1, page_size=20
            ),
            person_limit=limit,
            force_ann=True,
        )

        filtered_req = SearchPeopleRequest(
            required=SearchConditionBlock(jobs=["JOB-AI-DEV"]),
            page=1,
            page_size=20,
        )
        filtered_forced_ann = _run_pair(
            req=filtered_req, person_limit=limit, force_ann=True
        )
        filtered_router = _run_pair(
            req=filtered_req, person_limit=limit, force_ann=False
        )

        expanded = repo.validate_and_expand_codes(filtered_req)
        eligible_subq = repo.eligible_person_ids_subquery(
            required=filtered_req.required,
            expanded=expanded,
            skill_match_mode=filtered_req.skill_match_mode,
        )
        policy_hits, policy_trunc = repo.semantic_channel_hits(
            query_vector=qvec,
            eligible_subq=eligible_subq,
            limit=limit,
            force_exact=True,
        )

        depth_report = [
            _run_pair(
                req=SearchPeopleRequest(
                    required=SearchConditionBlock(), page=1, page_size=20
                ),
                person_limit=person_limit,
                force_ann=True,
            )
            for person_limit in (500, 2000, 5000)
        ]

        report = {
            "limit": limit,
            "recall_definition": (
                "Recall@K = |ExactTopK ∩ AnnTopK| / |ExactTopK| "
                "(both sides truncated to K)"
            ),
            "unfiltered_forced_ann": unfiltered,
            "filtered_job_ai_dev_forced_ann": filtered_forced_ann,
            "filtered_job_ai_dev_router_no_service_force": filtered_router,
            "filtered_required_force_exact_policy": {
                "count": len(policy_hits),
                "candidate_limit_reached": bool(policy_trunc),
                "note": (
                    "Production SearchQueryService passes force_exact=True when any "
                    "required hard filter is present; ANN is unused on that path."
                ),
            },
            "depth_pool_caps": depth_report,
            "policy": {
                "required_hard_filter": "force_exact",
                "ann_pool_saturation_sets_candidate_limit_reached": True,
                "exact_eligible_threshold": SEMANTIC_EXACT_ELIGIBLE_THRESHOLD,
            },
        }
        print(json.dumps(report, indent=2, default=str))
    finally:
        db.close()
        engine.dispose()



def _parse_int(value: str) -> int:
    """Accept decimal or 0x-prefixed hex (e.g. 0x612d)."""
    return int(value, 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="TalentScope hybrid search perf benchmark")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--recall", action="store_true")
    parser.add_argument("--people", type=int, default=2000)
    parser.add_argument("--projects-per-person", type=int, default=3)
    parser.add_argument("--chunks-per-person", type=int, default=10)
    parser.add_argument("--crowd-out-chunks", type=int, default=500)
    parser.add_argument("--seed-value", type=_parse_int, default=SEED_DEFAULT)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--out", type=str, default="/opt/cursor/artifacts/search-perf-before")
    args = parser.parse_args()

    if args.reset:
        perf_url = _require_perf_url(allow_write=True)
        _, engine = _session_factory(perf_url)
        _reset(engine)
        engine.dispose()
        print(json.dumps({"reset": True, "db": _db_name(perf_url)}))

    if args.seed:
        stats = seed_dataset(
            people=args.people,
            projects_per_person=args.projects_per_person,
            chunks_per_person=args.chunks_per_person,
            seed=args.seed_value,
            crowd_out_chunks=args.crowd_out_chunks,
        )
        print(json.dumps({"seeded": stats}, indent=2))

    if args.explain:
        run_explain(Path(args.out))

    if args.benchmark:
        run_benchmark(Path(args.out), iterations=args.iterations)

    if args.recall:
        run_recall()

    if not any([args.seed, args.reset, args.explain, args.benchmark, args.recall]):
        parser.print_help()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
