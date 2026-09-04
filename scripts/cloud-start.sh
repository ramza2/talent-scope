#!/usr/bin/env bash
#
# TalentScope Cloud Agent start step.
#
# Per-boot initialization: brings PostgreSQL and Redis online and waits until
# Postgres accepts connections. Dependency installation and schema loading live
# in scripts/cloud-install.sh, not here.
set -euo pipefail

PG_VERSION=16

# Remove stale pid files left by unclean container restarts.
if [ -f "/var/run/postgresql/${PG_VERSION}-main.pid" ]; then
  if ! sudo -u postgres pg_isready -q 2>/dev/null; then
    echo "Removed stale pid file."
    sudo rm -f "/var/run/postgresql/${PG_VERSION}-main.pid" || true
  fi
fi

if ! sudo -u postgres pg_isready -q 2>/dev/null; then
  echo "==> Starting PostgreSQL ${PG_VERSION} cluster"
  sudo pg_ctlcluster "${PG_VERSION}" main start || true
fi

echo "==> Ensuring Redis is running"
if command -v redis-server >/dev/null 2>&1; then
  if ! redis-cli ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
  fi
  if redis-cli ping >/dev/null 2>&1; then
    echo "Redis is ready on 127.0.0.1:6379"
  else
    echo "WARNING: Redis did not become ready (Celery broker unavailable)" >&2
  fi
else
  echo "WARNING: redis-server not installed" >&2
fi

for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then
    echo "PostgreSQL is ready on 127.0.0.1:5432"
    exit 0
  fi
  sleep 1
done

echo "PostgreSQL failed to become ready" >&2
sudo tail -n 40 "/var/log/postgresql/postgresql-${PG_VERSION}-main.log" 2>/dev/null || true
exit 1
