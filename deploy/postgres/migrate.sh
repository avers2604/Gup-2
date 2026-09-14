#!/usr/bin/env bash
set -euo pipefail

# Production migrations must never run as the long-lived application role.
# Supply these values from the infrastructure secret store, not the web
# process environment. POSTGRES_MIGRATION_HOST/PORT may point directly at the
# writable primary while normal traffic uses PgBouncer.
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_MIGRATION_USER:?POSTGRES_MIGRATION_USER is required}"
: "${POSTGRES_MIGRATION_PASSWORD:?POSTGRES_MIGRATION_PASSWORD is required}"
: "${POSTGRES_USER:?POSTGRES_USER (runtime role) is required}"
: "${GRAFANA_POSTGRES_USER:?GRAFANA_POSTGRES_USER is required}"

migration_host="${POSTGRES_MIGRATION_HOST:-${POSTGRES_HOST:-localhost}}"
migration_port="${POSTGRES_MIGRATION_PORT:-${POSTGRES_PORT:-5432}}"

POSTGRES_USER="$POSTGRES_MIGRATION_USER" \
POSTGRES_PASSWORD="$POSTGRES_MIGRATION_PASSWORD" \
POSTGRES_HOST="$migration_host" \
POSTGRES_PORT="$migration_port" \
python manage.py migrate "$@"

PGPASSWORD="$POSTGRES_MIGRATION_PASSWORD" psql \
  --host "$migration_host" \
  --port "$migration_port" \
  --username "$POSTGRES_MIGRATION_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=runtime_user="$POSTGRES_USER" \
  --set=grafana_user="$GRAFANA_POSTGRES_USER" \
  --file deploy/postgres/post-migrate-grants.sql
