#!/usr/bin/env bash
# Combined Stage 4 HA/DR drill evidence harness.
#
# This script deliberately does NOT inject failures, promote nodes, switch DNS,
# delete data, or run PITR. Those destructive actions remain explicit operator
# steps from the runbooks. The harness timestamps checkpoints and captures
# reproducible evidence before/after them.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRILL_ID="${DRILL_ID:-}"
DRILL_EVIDENCE_ROOT="${DRILL_EVIDENCE_ROOT:-/var/lib/bz-get/drills}"
PATRONI_CONFIG="${PATRONI_CONFIG:-/etc/patroni/patroni.yml}"
PATRONI_SCOPE="${PATRONI_SCOPE:-bz-get}"
PGBACKREST_BIN="${PGBACKREST_BIN:-pgbackrest}"
MINIO_DR_ENV="${MINIO_DR_ENV:-/etc/bz-get/minio-dr.env}"
ALERTMANAGER_URL="${ALERTMANAGER_URL:-}"
DRILL_INCIDENT_UTC="${DRILL_INCIDENT_UTC:-}"
DRILL_LAST_DURABLE_UTC="${DRILL_LAST_DURABLE_UTC:-}"
DRILL_SERVICE_RESTORED_UTC="${DRILL_SERVICE_RESTORED_UTC:-}"
DRILL_CHANGE_ID="${DRILL_CHANGE_ID:-}"
DRILL_OPERATOR="${DRILL_OPERATOR:-}"

usage() {
  cat <<'EOF'
Usage:
  combined-drill.sh preflight
  combined-drill.sh checkpoint NAME
  combined-drill.sh finish

Required: DRILL_ID from a sourced root-readable drill.env file.
EOF
}

[[ -n "$DRILL_ID" && "$DRILL_ID" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "ERROR: DRILL_ID must be set and contain only A-Z a-z 0-9 . _ -" >&2
  exit 64
}

run_dir="$DRILL_EVIDENCE_ROOT/$DRILL_ID"
checkpoints="$run_dir/checkpoints.csv"
mkdir -p "$run_dir"
chmod 0700 "$run_dir"

timestamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

capture_patroni() {
  patronictl -c "$PATRONI_CONFIG" list "$PATRONI_SCOPE" --format=json >"$run_dir/patroni-$1.json"
}

capture_pgbackrest() {
  "$PGBACKREST_BIN" --stanza="$PATRONI_SCOPE" info --output=json >"$run_dir/pgbackrest-$1.json"
}

capture_minio() {
  if [[ -r "$MINIO_DR_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$MINIO_DR_ENV"
    set +a
    bash "$SCRIPT_DIR/../minio/dr/check-replication.sh" >"$run_dir/minio-$1.txt" 2>&1
  else
    printf 'SKIPPED: MINIO_DR_ENV not readable: %s\n' "$MINIO_DR_ENV" >"$run_dir/minio-$1.txt"
  fi
}

capture_alertmanager() {
  if [[ -n "$ALERTMANAGER_URL" ]] && command -v curl >/dev/null 2>&1; then
    curl --fail --silent --show-error --max-time 10 \
      "${ALERTMANAGER_URL%/}/api/v2/status" >"$run_dir/alertmanager-$1.json"
  else
    printf 'SKIPPED: ALERTMANAGER_URL/curl unavailable\n' >"$run_dir/alertmanager-$1.txt"
  fi
}

record_checkpoint() {
  local name="$1"
  [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "ERROR: checkpoint name must contain only A-Z a-z 0-9 . _ -" >&2
    exit 64
  }
  if [[ ! -f "$checkpoints" ]]; then
    echo 'timestamp_utc,name' >"$checkpoints"
  fi
  printf '%s,%s\n' "$(timestamp)" "$name" >>"$checkpoints"
}

calculate_metrics() {
  python3 - "$DRILL_INCIDENT_UTC" "$DRILL_LAST_DURABLE_UTC" "$DRILL_SERVICE_RESTORED_UTC" <<'PY'
from datetime import datetime
import sys

def parse(value):
    if not value:
        return None
    if not value.endswith("Z"):
        raise SystemExit("drill timestamps must be explicit UTC and end with Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")

incident, durable, restored = map(parse, sys.argv[1:4])
if incident and durable:
    if durable > incident:
        raise SystemExit("DRILL_LAST_DURABLE_UTC cannot be after incident")
    print(f"observed_rpo_upper_bound_seconds={(incident-durable).total_seconds():.0f}")
else:
    print("observed_rpo_upper_bound_seconds=TBD")
if incident and restored:
    if restored < incident:
        raise SystemExit("DRILL_SERVICE_RESTORED_UTC cannot be before incident")
    print(f"observed_rto_seconds={(restored-incident).total_seconds():.0f}")
else:
    print("observed_rto_seconds=TBD")
PY
}

phase="${1:-}"
case "$phase" in
  preflight)
    for cmd in patronictl python3 "$PGBACKREST_BIN"; do
      command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: missing $cmd" >&2; exit 69; }
    done
    [[ -r "$PATRONI_CONFIG" ]] || { echo "ERROR: cannot read $PATRONI_CONFIG" >&2; exit 66; }
    record_checkpoint preflight-start
    capture_patroni preflight
    capture_pgbackrest preflight
    capture_minio preflight
    capture_alertmanager preflight
    {
      echo "drill_id=$DRILL_ID"
      echo "change_id=$DRILL_CHANGE_ID"
      echo "operator=$DRILL_OPERATOR"
      echo "preflight_utc=$(timestamp)"
    } >"$run_dir/metadata.env"
    record_checkpoint preflight-complete
    echo "Preflight evidence captured in $run_dir"
    ;;
  checkpoint)
    name="${2:-}"
    [[ -n "$name" ]] || { usage >&2; exit 64; }
    record_checkpoint "$name"
    echo "Recorded checkpoint '$name' at $(tail -n 1 "$checkpoints" | cut -d, -f1)"
    ;;
  finish)
    command -v python3 >/dev/null 2>&1 || { echo "ERROR: missing python3" >&2; exit 69; }
    record_checkpoint finish-start
    capture_patroni final
    capture_pgbackrest final
    capture_minio final
    capture_alertmanager final
    metrics="$(calculate_metrics)"
    {
      echo "# Stage 4 HA/DR drill result — $DRILL_ID"
      echo
      echo "- Change: ${DRILL_CHANGE_ID:-TBD}"
      echo "- Operator: ${DRILL_OPERATOR:-TBD}"
      echo "- Finished UTC: $(timestamp)"
      echo "- Incident UTC: ${DRILL_INCIDENT_UTC:-TBD}"
      echo "- Last durable UTC: ${DRILL_LAST_DURABLE_UTC:-TBD}"
      echo "- Service restored UTC: ${DRILL_SERVICE_RESTORED_UTC:-TBD}"
      while IFS='=' read -r key value; do
        echo "- $key: $value"
      done <<<"$metrics"
      echo
      echo "## Evidence"
      echo
      echo "See patroni-*.json, pgbackrest-*.json, minio-*.txt/json, alertmanager-* and checkpoints.csv in this directory."
      echo
      echo "## Acceptance"
      echo
      echo "PASS/FAIL: TBD — must be signed after business-data, object-version, WORM and alert-delivery validation."
    } >"$run_dir/RESULT.md"
    record_checkpoint finish-complete
    echo "Final evidence captured in $run_dir/RESULT.md"
    ;;
  *) usage >&2; exit 64 ;;
esac
