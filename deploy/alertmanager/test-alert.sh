#!/usr/bin/env bash
set -euo pipefail

severity="${1:-warning}"
case "$severity" in
  warning|critical) ;;
  *) echo "usage: $0 [warning|critical]" >&2; exit 2 ;;
esac

: "${ALERTMANAGER_URL:?set ALERTMANAGER_URL, e.g. http://alertmanager1:9093}"

starts_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ends_at="$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"

payload=$(cat <<JSON
[
  {
    "labels": {
      "alertname": "Stage4SyntheticDeliveryTest",
      "severity": "$severity",
      "cluster": "bz-get",
      "environment": "acceptance",
      "instance": "manual-drill"
    },
    "annotations": {
      "summary": "Synthetic Stage 4 alert delivery test",
      "description": "Manual test of Alertmanager $severity routing. Safe to resolve automatically after two minutes."
    },
    "startsAt": "$starts_at",
    "endsAt": "$ends_at",
    "generatorURL": "manual://stage4-alert-delivery-test"
  }
]
JSON
)

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -X POST \
  --data "$payload" \
  "${ALERTMANAGER_URL%/}/api/v2/alerts"

echo "Synthetic $severity alert submitted; it expires automatically at $ends_at."
