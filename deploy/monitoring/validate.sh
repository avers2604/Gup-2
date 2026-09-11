#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUSINESS_DASHBOARD="$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-business.json"

python3 -m py_compile "$ROOT_DIR/deploy/monitoring/pgbackrest_exporter.py"
python3 -m json.tool \
  "$ROOT_DIR/deploy/grafana/provisioning/dashboards/json/stage4-ha-dr.json" \
  >/dev/null
python3 -m json.tool "$BUSINESS_DASHBOARD" >/dev/null

for metric in \
  bz_get_ocr_review_overdue_total \
  bz_get_templates_revision_overdue_total \
  bz_get_search_zero_results_total \
  bz_get_search_requests_total \
  bz_get_link_generation_failures_total; do
  grep -q "$metric" "$BUSINESS_DASHBOARD"
done

# Business counters are persisted for the lifetime of the installation. A
# dashboard with a selectable time range must derive period values with
# increase(), not display the lifetime ratio/absolute counter as if it belonged
# to the selected period.
python3 - "$BUSINESS_DASHBOARD" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
panels = {panel.get("id"): panel for panel in payload.get("panels", [])}
search = panels[3]["targets"][0]["expr"]
if "bz_get_search_zero_result_ratio" in search:
    raise SystemExit("business dashboard must not use the lifetime zero-result ratio")
for required in (
    "increase(bz_get_search_zero_results_total[$__range])",
    "increase(bz_get_search_requests_total[$__range])",
):
    if required not in search:
        raise SystemExit(f"search period expression missing {required}")

link_targets = panels[4].get("targets", [])
if len(link_targets) != 3:
    raise SystemExit("link-failure panel must contain 403/404/504 series")
for target in link_targets:
    expr = target.get("expr", "")
    if "increase(bz_get_link_generation_failures_total" not in expr or "[$__range]" not in expr:
        raise SystemExit(f"link failure expression is not period-scoped: {expr}")
PY

grep -q 'job_name: bz_get_business' "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"
grep -q 'metrics_path: /metrics/business/' "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"

grep -q 'alert: WormPromotionStuck' "$ROOT_DIR/deploy/prometheus/rules/application.yml"
grep -q 'bz_get_worm_promotion_oldest_seconds.*> 900' "$ROOT_DIR/deploy/prometheus/rules/application.yml"
grep -q 'alert: WormPromotionBacklog' "$ROOT_DIR/deploy/prometheus/rules/application.yml"

if command -v promtool >/dev/null 2>&1; then
  promtool check config "$ROOT_DIR/deploy/prometheus/prometheus.stage4.yml.example"
  promtool check rules "$ROOT_DIR/deploy/prometheus/rules/ha-dr.yml"
  promtool check rules "$ROOT_DIR/deploy/prometheus/rules/application.yml"
else
  echo "WARN: promtool not installed; Prometheus semantic validation skipped" >&2
fi

echo "Stage 4 monitoring static validation passed"
