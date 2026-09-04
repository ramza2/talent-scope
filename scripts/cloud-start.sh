#!/usr/bin/env bash
#
# TalentScope Cloud Agent start step.
#
# Per-boot initialization: brings the PostgreSQL cluster online and waits until
# it accepts connections. Dependency installation and schema loading live in
# scripts/cloud-install.sh, not here.
set -euo pipefail

PG_VERSION=16

if ! sudo -u postgres pg_isready -q 2>/dev/null; then
  echo "==> Starting PostgreSQL ${PG_VERSION} cluster"
  sudo pg_ctlcluster "${PG_VERSION}" main start
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
