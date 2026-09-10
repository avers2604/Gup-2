#!/usr/bin/env bash
# Stage 4 real-stand acceptance evidence harness.
#
# The harness is intentionally non-destructive: it validates the environment,
# records checkpoints and finalizes measured RPO/RTO. Failure injection,
# promotion, PITR and DNS/LB changes remain explicit operator actions from the
# approved runbooks.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ACCEPTANCE_ENV="${ACCEPTANCE_ENV:-/etc/bz-get/stage4-acceptance.env}"

usage() {
  cat <<'EOF'
Usage:
  acceptance-cycle.sh RUN_ID preflight
  acceptance-cycle.sh RUN_ID checkpoint NAME
  acceptance-cycle.sh RUN_ID finalize

Environment:
  ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env

Finalization timestamps (UTC, ISO-8601 with Z):
  ACCEPTANCE_INCIDENT_UTC
  ACCEPTANCE_LAST_DURABLE_UTC
  ACCEPTANCE_SERVICE_RESTORED_UTC

Manual acceptance flags for finalize (PASS or FAIL):
  ACCEPTANCE_DB_RESULT
  ACCEPTANCE_MINIO_RESULT
  ACCEPTANCE_ALERT_RESULT
  ACCEPTANCE_APP_RESULT
EOF
}

run_id="${1:-}"
action="${2:-}"
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] || { usage >&2; exit 64; }
[[ -r "$ACCEPTANCE_ENV" ]] || {
  echo "ERROR: acceptance inventory is not readable: $ACCEPTANCE_ENV" >&2
  exit 66
}

set -a
# shellcheck disable=SC1090
. "$ACCEPTANCE_ENV"
set +a

: "${PATRONI_CONFIG:?PATRONI_CONFIG is required}"
: "${PATRONI_SCOPE:?PATRONI_SCOPE is required}"
: "${EXPECTED_DB_MEMBERS:?EXPECTED_DB_MEMBERS is required}"
: "${ETCD_ENDPOINTS:?ETCD_ENDPOINTS is required}"
: "${ETCD_CACERT:?ETCD_CACERT is required}"
: "${ETCD_CERT:?ETCD_CERT is required}"
: "${ETCD_KEY:?ETCD_KEY is required}"
: "${PGBACKREST_STANZA:?PGBACKREST_STANZA is required}"
: "${MINIO_DR_ENV:?MINIO_DR_ENV is required}"
: "${ALERTMANAGER_URLS:?ALERTMANAGER_URLS is required}"
: "${APP_HEALTH_URL:?APP_HEALTH_URL is required}"
: "${ACCEPTANCE_EVIDENCE_ROOT:?ACCEPTANCE_EVIDENCE_ROOT is required}"

PGBACKREST_BIN="${PGBACKREST_BIN:-pgbackrest}"
MAX_REPLICA_LAG_BYTES="${MAX_REPLICA_LAG_BYTES:-1048576}"
MAX_BACKUP_AGE_SECONDS="${MAX_BACKUP_AGE_SECONDS:-90000}"
APP_HEALTH_EXPECT_REGEX="${APP_HEALTH_EXPECT_REGEX:-^(ok|healthy|ready)$}"

run_dir="$ACCEPTANCE_EVIDENCE_ROOT/$run_id"
mkdir -p "$run_dir"
chmod 0700 "$run_dir"
checkpoints="$run_dir/checkpoints.csv"

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

record_checkpoint() {
  local name="$1"
  [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "ERROR: invalid checkpoint name: $name" >&2
    exit 64
  }
  if [[ ! -f "$checkpoints" ]]; then
    echo 'timestamp_utc,name' >"$checkpoints"
  fi
  printf '%s,%s\n' "$(now_utc)" "$name" >>"$checkpoints"
}

require_commands() {
  local cmd
  for cmd in patronictl etcdctl curl python3 "$PGBACKREST_BIN"; do
    command -v "$cmd" >/dev/null 2>&1 || {
      echo "ERROR: required command not found: $cmd" >&2
      exit 69
    }
  done
}

check_patroni() {
  local output="$run_dir/patroni-preflight.json"
  patronictl -c "$PATRONI_CONFIG" list "$PATRONI_SCOPE" --format=json >"$output"
  python3 - "$output" "$EXPECTED_DB_MEMBERS" "$MAX_REPLICA_LAG_BYTES" <<'PY'
import json
import sys
from pathlib import Path

path, expected_raw, max_lag_raw = sys.argv[1:]
rows = json.loads(Path(path).read_text())
if not isinstance(rows, list) or not rows:
    raise SystemExit("Patroni topology is empty")

def norm(row):
    return {str(k).strip().lower(): v for k, v in row.items()}

rows = [norm(r) for r in rows]
expected = {x.strip() for x in expected_raw.split(",") if x.strip()}
actual = {str(r.get("member", "")).strip() for r in rows}
if actual != expected:
    raise SystemExit(f"Patroni members mismatch: expected={sorted(expected)} actual={sorted(actual)}")

leaders = [r for r in rows if str(r.get("role", "")).strip().lower() in {"leader", "primary"}]
if len(leaders) != 1:
    raise SystemExit(f"expected exactly one leader/primary, found {len(leaders)}")

max_lag = int(max_lag_raw)
for row in rows:
    role = str(row.get("role", "")).strip().lower()
    state = str(row.get("state", "")).strip().lower()
    if role in {"replica", "standby", "sync standby", "sync standby*"} or "replica" in role or "standby" in role:
        if state not in {"running", "streaming"}:
            raise SystemExit(f"replica {row.get('member')} is not healthy: state={state!r}")
        raw = row.get("lag in mb", row.get("lag", 0))
        if raw in (None, "", "-"):
            raw = 0
        try:
            lag_bytes = float(raw) * 1024 * 1024
        except (TypeError, ValueError):
            raise SystemExit(f"cannot parse replica lag for {row.get('member')}: {raw!r}")
        if lag_bytes > max_lag:
            raise SystemExit(
                f"replica {row.get('member')} lag {lag_bytes:.0f} exceeds {max_lag} bytes"
            )
print(f"Patroni OK: leader={leaders[0].get('member')} members={sorted(actual)}")
PY
}

check_etcd() {
  local out="$run_dir/etcd-health.txt"
  ETCDCTL_API=3 etcdctl \
    --endpoints="$ETCD_ENDPOINTS" \
    --cacert="$ETCD_CACERT" \
    --cert="$ETCD_CERT" \
    --key="$ETCD_KEY" \
    endpoint health --cluster >"$out" 2>&1
  local expected_count healthy_count
  expected_count="$(awk -F, '{print NF}' <<<"$ETCD_ENDPOINTS")"
  healthy_count="$(grep -c 'is healthy' "$out" || true)"
  if [[ "$healthy_count" -ne "$expected_count" ]]; then
    cat "$out" >&2
    echo "ERROR: etcd health mismatch: healthy=$healthy_count expected=$expected_count" >&2
    exit 1
  fi
  echo "etcd OK: $healthy_count/$expected_count endpoints healthy"
}

check_pgbackrest() {
  local out="$run_dir/pgbackrest-preflight.json"
  "$PGBACKREST_BIN" --stanza="$PGBACKREST_STANZA" check >"$run_dir/pgbackrest-check.txt" 2>&1
  "$PGBACKREST_BIN" --stanza="$PGBACKREST_STANZA" info --output=json >"$out"
  python3 - "$out" "$MAX_BACKUP_AGE_SECONDS" <<'PY'
import json
import sys
import time
from pathlib import Path

path, max_age_raw = sys.argv[1:]
payload = json.loads(Path(path).read_text())
if not isinstance(payload, list) or not payload:
    raise SystemExit("pgBackRest returned no stanza")
stanza = payload[0]
status = stanza.get("status", {})
if status.get("code") not in (0, None):
    raise SystemExit(f"pgBackRest stanza unhealthy: {status}")
backups = stanza.get("backup") or []
if not backups:
    raise SystemExit("pgBackRest repository has no backup sets")
latest = backups[-1]
stop = (latest.get("timestamp") or {}).get("stop")
if stop is None:
    raise SystemExit("latest pgBackRest backup has no timestamp.stop")
age = time.time() - float(stop)
if age < 0:
    raise SystemExit("latest pgBackRest backup timestamp is in the future")
max_age = int(max_age_raw)
if age > max_age:
    raise SystemExit(f"latest backup is too old: age={age:.0f}s max={max_age}s")
print(f"pgBackRest OK: latest={latest.get('label', 'unknown')} age={age:.0f}s")
PY
}

check_minio() {
  [[ -r "$MINIO_DR_ENV" ]] || {
    echo "ERROR: MinIO DR env is not readable: $MINIO_DR_ENV" >&2
    exit 66
  }
  set -a
  # shellcheck disable=SC1090
  . "$MINIO_DR_ENV"
  set +a
  bash "$REPO_ROOT/deploy/minio/dr/check-replication.sh" >"$run_dir/minio-preflight.txt" 2>&1
  echo "MinIO DR OK"
}

check_alertmanager() {
  local urls="$ALERTMANAGER_URLS"
  local total=0
  local ok=0
  IFS=',' read -r -a am_urls <<<"$urls"
  : >"$run_dir/alertmanager-preflight.txt"
  local url
  for url in "${am_urls[@]}"; do
    url="${url//[[:space:]]/}"
    [[ -n "$url" ]] || continue
    total=$((total + 1))
    if curl --fail --silent --show-error --max-time 10 "${url%/}/api/v2/status" \
      >"$run_dir/alertmanager-$total.json"; then
      ok=$((ok + 1))
      echo "$url OK" >>"$run_dir/alertmanager-preflight.txt"
    else
      echo "$url FAIL" >>"$run_dir/alertmanager-preflight.txt"
    fi
  done
  if [[ "$total" -lt 2 || "$ok" -ne "$total" ]]; then
    cat "$run_dir/alertmanager-preflight.txt" >&2
    echo "ERROR: Alertmanager HA preflight failed: ok=$ok total=$total" >&2
    exit 1
  fi
  echo "Alertmanager OK: $ok/$total endpoints reachable"
}

check_app() {
  local body="$run_dir/app-health.txt"
  curl --fail --silent --show-error --max-time 15 "$APP_HEALTH_URL" >"$body"
  local compact
  compact="$(tr -d '\r\n' <"$body")"
  if ! grep -Eiq "$APP_HEALTH_EXPECT_REGEX" <<<"$compact"; then
    echo "ERROR: application health response did not match expected regex" >&2
    echo "response=$compact" >&2
    exit 1
  fi
  echo "Application OK"
}

finalize_report() {
  local db_result="${ACCEPTANCE_DB_RESULT:-}"
  local minio_result="${ACCEPTANCE_MINIO_RESULT:-}"
  local alert_result="${ACCEPTANCE_ALERT_RESULT:-}"
  local app_result="${ACCEPTANCE_APP_RESULT:-}"
  local incident="${ACCEPTANCE_INCIDENT_UTC:-}"
  local durable="${ACCEPTANCE_LAST_DURABLE_UTC:-}"
  local restored="${ACCEPTANCE_SERVICE_RESTORED_UTC:-}"

  for value in "$db_result" "$minio_result" "$alert_result" "$app_result"; do
    [[ "$value" == "PASS" || "$value" == "FAIL" ]] || {
      echo "ERROR: all ACCEPTANCE_*_RESULT flags must be PASS or FAIL" >&2
      exit 64
    }
  done

  python3 - "$incident" "$durable" "$restored" >"$run_dir/metrics.env" <<'PY'
from datetime import datetime
import sys

def parse(value, name):
    if not value or not value.endswith("Z"):
        raise SystemExit(f"{name} must be explicit UTC and end with Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")

incident = parse(sys.argv[1], "ACCEPTANCE_INCIDENT_UTC")
durable = parse(sys.argv[2], "ACCEPTANCE_LAST_DURABLE_UTC")
restored = parse(sys.argv[3], "ACCEPTANCE_SERVICE_RESTORED_UTC")
if durable > incident:
    raise SystemExit("last durable time cannot be after incident")
if restored < incident:
    raise SystemExit("service restored time cannot be before incident")
print(f"observed_rpo_seconds={(incident-durable).total_seconds():.0f}")
print(f"observed_rto_seconds={(restored-incident).total_seconds():.0f}")
PY

  local overall=PASS
  [[ "$db_result" == PASS && "$minio_result" == PASS && "$alert_result" == PASS && "$app_result" == PASS ]] || overall=FAIL

  {
    echo "# Stage 4 acceptance result — $run_id"
    echo
    echo "- Overall: **$overall**"
    echo "- Site: ${ACCEPTANCE_SITE:-TBD}"
    echo "- Change: ${ACCEPTANCE_CHANGE_ID:-TBD}"
    echo "- Operator: ${ACCEPTANCE_OPERATOR:-TBD}"
    echo "- Finalized UTC: $(now_utc)"
    echo "- DB/Patroni: $db_result"
    echo "- MinIO DR: $minio_result"
    echo "- Alert delivery: $alert_result"
    echo "- Application: $app_result"
    echo "- Incident UTC: $incident"
    echo "- Last durable UTC: $durable"
    echo "- Service restored UTC: $restored"
    while IFS='=' read -r k v; do echo "- $k: $v"; done <"$run_dir/metrics.env"
    echo
    echo "## Evidence"
    echo
    echo "Evidence directory: $run_dir"
    echo
    echo "Required manual evidence: failover/switchover commands, PITR target and validation, MinIO object/version/checksum checks, synthetic alert delivery confirmation and application smoke-test results."
    echo
    echo "## Decision"
    echo
    echo "This generated result is technical evidence only. Production RPO/RTO/SLA acceptance requires the designated customer/operations approver."
  } >"$run_dir/RESULT.md"
  record_checkpoint finalized
  echo "Acceptance result: $overall; report=$run_dir/RESULT.md"
  [[ "$overall" == PASS ]]
}

case "$action" in
  preflight)
    require_commands
    record_checkpoint preflight-start
    {
      echo "run_id=$run_id"
      echo "site=${ACCEPTANCE_SITE:-}"
      echo "change_id=${ACCEPTANCE_CHANGE_ID:-}"
      echo "operator=${ACCEPTANCE_OPERATOR:-}"
      echo "started_utc=$(now_utc)"
    } >"$run_dir/metadata.env"
    check_patroni
    check_etcd
    check_pgbackrest
    check_minio
    check_alertmanager
    check_app
    record_checkpoint preflight-complete
    echo "Stage 4 acceptance preflight PASSED; evidence=$run_dir"
    ;;
  checkpoint)
    name="${3:-}"
    [[ -n "$name" ]] || { usage >&2; exit 64; }
    record_checkpoint "$name"
    echo "Checkpoint recorded: $name"
    ;;
  finalize)
    finalize_report
    ;;
  *)
    usage >&2
    exit 64
    ;;
esac
