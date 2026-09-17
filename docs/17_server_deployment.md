# 17. Server Deployment / Migration / Pytest / PERF Runbook

## 1. Target environment

This runbook is for the current OpenLink server where a shared Traefik v3 instance is already running.

Confirmed routing values:

```text
Host              talentscope.openlink.kr
Traefik network   traefik_proxy
HTTPS entrypoint  websecure
ACME resolver     letsencrypt
HTTP challenge    web
```

The server Compose file is `docker-compose.server.yml`.

Routing:

```text
https://talentscope.openlink.kr/       -> frontend:80
https://talentscope.openlink.kr/api/*  -> api:8000
```

Only `frontend` and `api` join `traefik_proxy`. PostgreSQL, Redis, MinIO, worker, beat, migration, pytest and PERF containers stay on the TalentScope private Docker network.

---

## 2. DNS prerequisite

Before public HTTPS validation, ensure DNS points the host to this server.

```text
A  talentscope.openlink.kr  -> <server public IP>
```

Verify from the server or another host:

```bash
getent hosts talentscope.openlink.kr
# or
nslookup talentscope.openlink.kr
```

Do not continue public TLS troubleshooting until DNS resolves to the intended server.

---

## 3. Checkout

For PR #21 verification, deploy the PR branch rather than `main`:

```bash
git fetch origin
git switch cursor/hybrid-search-perf-612d
git pull --ff-only origin cursor/hybrid-search-perf-612d
git rev-parse HEAD
```

After PR #21 is merged, normal deployments should use the merged `main` revision.

---

## 4. Server environment file

Create the real server environment file from the tracked template:

```bash
cp .env.server.example .env.server
chmod 600 .env.server
vi .env.server
```

At minimum replace every `change-me` value.

Generate a strong application secret, for example:

```bash
openssl rand -hex 32
```

The committed template intentionally contains no real secrets.

Expected Traefik values are already set in the template:

```text
TALENTSCOPE_HOST=talentscope.openlink.kr
TRAEFIK_NETWORK=traefik_proxy
TRAEFIK_ENTRYPOINT=websecure
TRAEFIK_CERTRESOLVER=letsencrypt
```

Before enabling semantic search, configure the real embedding endpoint/key and set:

```text
EMBEDDING_ENABLED=true
```

Keep it `false` if the endpoint is not ready. Structured/keyword search remains available.

---

## 5. Preflight

Confirm the shared Traefik network exists:

```bash
docker network inspect traefik_proxy >/dev/null && echo "traefik_proxy OK"
```

Validate Compose interpolation before creating containers:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  config >/tmp/talentscope-compose.rendered.yml
```

Review service names without printing secrets:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  config --services
```

Expected core services:

```text
postgres
redis
minio
minio-init
api
worker
beat
frontend
```

Tool-profile services:

```text
migrate
tools-db-init
test
perf
```

---

## 6. Build

Build the application images from the checked-out revision:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  build api worker beat frontend migrate test perf
```

The backend runtime image installs normal application dependencies. The one-shot `test` service additionally installs the `[dev]` extras inside its disposable container before running pytest.

---

## 7. Start infrastructure first

Start private dependencies before schema migration:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  up -d postgres redis minio
```

Create the MinIO bucket idempotently:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  run --rm minio-init
```

Check infrastructure status:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  ps
```

---

## 8. Production DB migration

Use the internal-only migration service. Do not run Alembic through the Traefik-facing API service container.

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm migrate
```

This executes:

```text
alembic upgrade head
```

against `${POSTGRES_DB}` only.

Verify Alembic revision if needed:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm migrate alembic current
```

If the second command is used, note that Compose `run` replaces the service command.

---

## 9. Start application services

After migration succeeds:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  up -d api worker beat frontend
```

Check status:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  ps
```

Check recent logs:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  logs --tail=100 api worker beat frontend
```

---

## 10. Health / Traefik / TLS verification

Backend liveness through Traefik:

```bash
curl -fsS https://talentscope.openlink.kr/api/v1/health/live
```

Backend readiness:

```bash
curl -fsS https://talentscope.openlink.kr/api/v1/health/ready
```

Frontend:

```bash
curl -I https://talentscope.openlink.kr/
```

Confirm Traefik labels if routing fails:

```bash
docker inspect $(docker compose --env-file .env.server -f docker-compose.server.yml ps -q api) \
  --format '{{json .Config.Labels}}' | jq

docker inspect $(docker compose --env-file .env.server -f docker-compose.server.yml ps -q frontend) \
  --format '{{json .Config.Labels}}' | jq
```

Confirm both containers joined the external network:

```bash
docker inspect $(docker compose --env-file .env.server -f docker-compose.server.yml ps -q api) \
  --format '{{json .NetworkSettings.Networks}}' | jq
docker inspect $(docker compose --env-file .env.server -f docker-compose.server.yml ps -q frontend) \
  --format '{{json .NetworkSettings.Networks}}' | jq
```

For certificate issuance problems, inspect the existing shared Traefik logs:

```bash
docker logs --tail=200 traefik
```

---

## 11. Isolated pytest database

Do not run the integration suite against `${POSTGRES_DB}`.

The tool profile uses:

```text
POSTGRES_TEST_DB=talentscope_test
```

and `tools-db-init` creates it idempotently.

Run the full backend suite:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm test
```

The test service performs, in order:

```text
pip install .[dev]
alembic upgrade head      # against talentscope_test
pytest -p no:cacheprovider
```

Run only the PR #21 semantic policy tests:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm test \
  sh -lc "python -m pip install '.[dev]' && alembic upgrade head && pytest -q tests/test_search_semantic_perf_policy.py tests/test_hybrid_search.py"
```

`test` is disposable; the test database is separate but persistent until explicitly dropped or the PostgreSQL volume is removed.

---

## 12. PERF database preparation

PERF must use a third database, never the product or pytest DB:

```text
POSTGRES_PERF_DB=talentscope_perf
```

The benchmark itself has additional safety checks:

- `PERF_DATABASE_URL` is required.
- destructive seed/reset refuses `PERF_DATABASE_URL == DATABASE_URL`.
- write targets must contain `_perf` or `_test` in the DB name.

Create the tool databases idempotently:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm tools-db-init
```

Apply the application schema to the PERF DB:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  sh -lc 'DATABASE_URL="$PERF_DATABASE_URL" alembic upgrade head'
```

---

## 13. PERF 2k baseline

Create/refresh the deterministic 2k fixture:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  python scripts/search_perf_benchmark.py --reset

docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  python scripts/search_perf_benchmark.py --seed --people 2000 --seed-value 0x612d
```

Run benchmark / recall / EXPLAIN artifacts:

```bash
mkdir -p artifacts/search-perf-after

docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  python scripts/search_perf_benchmark.py \
    --benchmark --iterations 5 --out /artifacts/search-perf-after

docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  python scripts/search_perf_benchmark.py --recall

docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  --profile tools \
  run --rm perf \
  python scripts/search_perf_runtime_explain.py \
    --limit 500 --iterations 5 \
    --out /artifacts/search-perf-after/runtime-ann-explain.json
```

The host receives artifacts under:

```text
./artifacts/search-perf-after/
```

Do not copy benchmark-generated timing values into docs unless the commands actually ran successfully on that dataset/environment.

---

## 14. Optional 5k / 10k crossover measurement

PR #21 currently keeps production semantic retrieval exact-first because the measured 2k fixture did not prove ANN faster.

To evaluate a larger dataset, reset/reseed the isolated PERF DB and repeat the same commands:

```bash
# 5k
docker compose --env-file .env.server -f docker-compose.server.yml --profile tools \
  run --rm perf python scripts/search_perf_benchmark.py --reset
docker compose --env-file .env.server -f docker-compose.server.yml --profile tools \
  run --rm perf python scripts/search_perf_benchmark.py --seed --people 5000 --seed-value 0x612d

# 10k (only if server resources allow it)
docker compose --env-file .env.server -f docker-compose.server.yml --profile tools \
  run --rm perf python scripts/search_perf_benchmark.py --reset
docker compose --env-file .env.server -f docker-compose.server.yml --profile tools \
  run --rm perf python scripts/search_perf_benchmark.py --seed --people 10000 --seed-value 0x612d
```

For each scale, record:

- people / projects / SearchIndexItem counts,
- exact median / p95,
- ANN median / p95,
- production-shaped HNSW plan,
- Recall@10 / 50 / 100,
- requested versus actual pool rows,
- candidate underfill,
- PostgreSQL / pgvector version.

Do not enable production ANN only because HNSW exists; enable it only after a measured crossover with acceptable recall.

---

## 15. Update deployment

For a later code update:

```bash
git pull --ff-only

docker compose --env-file .env.server -f docker-compose.server.yml build api worker beat frontend migrate test perf

docker compose --env-file .env.server -f docker-compose.server.yml up -d postgres redis minio

docker compose --env-file .env.server -f docker-compose.server.yml --profile tools run --rm migrate

docker compose --env-file .env.server -f docker-compose.server.yml up -d api worker beat frontend
```

Check logs and health after every migration/deploy.

---

## 16. Stop / remove

Stop application containers while preserving volumes:

```bash
docker compose --env-file .env.server -f docker-compose.server.yml down
```

Do **not** add `-v` unless PostgreSQL/Redis/MinIO persistent data is intentionally being destroyed.

Never use the PERF reset command against the product DB.
