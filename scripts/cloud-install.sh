#!/usr/bin/env bash
#
# TalentScope Cloud Agent install step.
#
# Provisions the MVP data layer described in docs/13_application_architecture.md:
# PostgreSQL 16 with the pgvector, pg_trgm and pgcrypto extensions, then applies
# the baseline schema in db/schema.sql to a dev database.
#
# This script is idempotent: it can run repeatedly and converges to the same
# state. It only (re)applies the schema when the target database has no tables,
# so existing data is never destroyed on re-run.
set -euo pipefail

PG_VERSION=16
DB_NAME="${TALENTSCOPE_DB_NAME:-talentscope}"
DB_USER="${TALENTSCOPE_DB_USER:-talentscope}"
DB_PASSWORD="${TALENTSCOPE_DB_PASSWORD:-talentscope}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SCHEMA_FILE="${REPO_ROOT}/db/schema.sql"

echo "==> Installing PostgreSQL ${PG_VERSION} + pgvector + contrib"
if ! dpkg -s "postgresql-${PG_VERSION}" >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    "postgresql-${PG_VERSION}" \
    postgresql-contrib \
    "postgresql-${PG_VERSION}-pgvector"
else
  echo "    already installed, skipping apt-get"
fi

echo "==> Ensuring PostgreSQL cluster is running"
if ! sudo -u postgres pg_isready -q 2>/dev/null; then
  # invoke-rc.d is blocked inside the container; start the cluster directly.
  sudo pg_ctlcluster "${PG_VERSION}" main start || true
fi
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done
sudo -u postgres pg_isready

echo "==> Ensuring role '${DB_USER}' exists"
if ! sudo -u postgres psql -tAqc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
    "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';"
fi

echo "==> Ensuring database '${DB_NAME}' exists"
if ! sudo -u postgres psql -tAqc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
fi

TABLE_COUNT="$(sudo -u postgres psql -tAqc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" \
  "${DB_NAME}")"

if [ "${TABLE_COUNT}" = "0" ]; then
  echo "==> Applying baseline schema (db/schema.sql)"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SCHEMA_FILE}"
  echo "==> Granting privileges to '${DB_USER}'"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
GRANT ALL ON SCHEMA public TO ${DB_USER};
GRANT ALL ON ALL TABLES IN SCHEMA public TO ${DB_USER};
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${DB_USER};
SQL
else
  echo "==> Schema already present (${TABLE_COUNT} tables), skipping load"
fi

echo "==> TalentScope dev database ready:"
echo "    postgresql://${DB_USER}:***@127.0.0.1:5432/${DB_NAME}"
