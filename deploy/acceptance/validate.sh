#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$SCRIPT_DIR/acceptance-cycle.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/evidence"
touch "$tmp/patroni.yml" "$tmp/ca.crt" "$tmp/etcd.crt" "$tmp/etcd.key" "$tmp/minio.env"

cat >"$tmp/bin/patronictl" <<'EOF'
#!/usr/bin/env bash
cat <<'JSON'
[
  {"Member":"db1","Role":"Leader","State":"running","Lag in MB":0},
  {"Member":"db2","Role":"Replica","State":"streaming","Lag in MB":0},
  {"Member":"db3","Role":"Replica","State":"streaming","Lag in MB":0}
]
JSON
EOF
chmod +x "$tmp/bin/patronictl"

cat >"$tmp/bin/etcdctl" <<'EOF'
#!/usr/bin/env bash
echo 'https://etcd1:2379 is healthy: successfully committed proposal'
echo 'https://etcd2:2379 is healthy: successfully committed proposal'
echo 'https://etcd3:2379 is healthy: successfully committed proposal'
EOF
chmod +x "$tmp/bin/etcdctl"

cat >"$tmp/bin/pgbackrest" <<'EOF'
#!/usr/bin/env bash
if [[ " $* " == *" info "* ]]; then
  now="$(date +%s)"
  printf '[{"name":"bz-get","status":{"code":0},"backup":[{"label":"ci-full","timestamp":{"stop":%s}}]}]\n' "$now"
  exit 0
fi
if [[ " $* " == *" check "* ]]; then
  echo 'stanza: bz-get status: ok'
  exit 0
fi
exit 1
EOF
chmod +x "$tmp/bin/pgbackrest"

cat >"$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
url="${!#}"
case "$url" in
  */api/v2/status) echo '{"cluster":{"status":"ready"}}' ;;
  */health/) echo 'healthy' ;;
  *) echo "unexpected URL: $url" >&2; exit 22 ;;
esac
EOF
chmod +x "$tmp/bin/curl"

cat >"$tmp/minio-check.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo 'MinIO replication OK'
EOF
chmod +x "$tmp/minio-check.sh"

cat >"$tmp/acceptance.env" <<EOF
PATRONI_CONFIG=$tmp/patroni.yml
PATRONI_SCOPE=bz-get
EXPECTED_DB_MEMBERS=db1,db2,db3
MAX_REPLICA_LAG_BYTES=1048576
ETCD_ENDPOINTS=https://etcd1:2379,https://etcd2:2379,https://etcd3:2379
ETCD_CACERT=$tmp/ca.crt
ETCD_CERT=$tmp/etcd.crt
ETCD_KEY=$tmp/etcd.key
PGBACKREST_BIN=pgbackrest
PGBACKREST_STANZA=bz-get
MAX_BACKUP_AGE_SECONDS=3600
MINIO_DR_ENV=$tmp/minio.env
MINIO_CHECK_SCRIPT=$tmp/minio-check.sh
ALERTMANAGER_URLS=http://am1:9093,http://am2:9093
APP_HEALTH_URL=http://app/health/
APP_HEALTH_EXPECT_REGEX=healthy
ACCEPTANCE_EVIDENCE_ROOT=$tmp/evidence
ACCEPTANCE_CHANGE_ID=CI-42
ACCEPTANCE_OPERATOR=ci
ACCEPTANCE_SITE=ci-stand
EOF

export PATH="$tmp/bin:$PATH"
export ACCEPTANCE_ENV="$tmp/acceptance.env"

bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 preflight >/dev/null
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 checkpoint planned-switchover-complete >/dev/null
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 checkpoint pitr-validated >/dev/null

env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T19:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T18:59:50Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T19:03:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 finalize >/dev/null

grep -q 'Overall: \*\*PASS\*\*' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'observed_rpo_seconds: 10' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'observed_rto_seconds: 180' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'planned-switchover-complete' "$tmp/evidence/ci-stage4/checkpoints.csv"
grep -q 'pitr-validated' "$tmp/evidence/ci-stage4/checkpoints.csv"

# A failed subsystem must make finalize fail closed and report overall FAIL.
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=FAIL \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T20:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T19:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T20:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-fail finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted a failed subsystem' >&2
  exit 1
fi
grep -q 'Overall: \*\*FAIL\*\*' "$tmp/evidence/ci-stage4-fail/RESULT.md"

echo 'Stage 4 acceptance harness validation passed.'
