#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 -m py_compile "$ROOT_DIR/deploy/monitoring/pgbackrest_exporter.py"
python3 -m json.tool \
  "$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-ha-dr.json" \
  >/dev/null

if command -v promtool >/dev/null 2>&1; then
  promtool check config "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"
  promtool check rules "$ROOT_DIR/deploy/prometheus/rules/ha-dr.yml"
else
  echo "WARN: promtool not installed; Prometheus semantic validation skipped" >&2
fi

echo "Stage 4 monitoring static validation passed"
