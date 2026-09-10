#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 -m py_compile "$ROOT_DIR/deploy/monitoring/pgbackrest_exporter.py"
python3 -m json.tool \
  "$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-ha-dr.json" \
  >/dev/null
python3 -m json.tool \
  "$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-business.json" \
  >/dev/null

for metric in \
  bz_get_ocr_review_overdue_total \
  bz_get_templates_revision_overdue_total \
  bz_get_search_zero_result_ratio \
  bz_get_link_generation_failures_total; do
  grep -q "$metric" "$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-business.json"
done

grep -q 'job_name: bz_get_business' "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"
grep -q 'metrics_path: /metrics/business/' "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"

if command -v promtool >/dev/null 2>&1; then
  promtool check config "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"
  promtool check rules "$ROOT_DIR/deploy/prometheus/rules/ha-dr.yml"
else
  echo "WARN: promtool not installed; Prometheus semantic validation skipped" >&2
fi

echo "Stage 4 monitoring static validation passed"
