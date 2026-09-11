#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$SCRIPT_DIR/acceptance-cycle.sh"
bash -n "$SCRIPT_DIR/extended-checks.sh"
python3 -m py_compile \
  "$SCRIPT_DIR/acceptance_policy.py" "$SCRIPT_DIR/test_acceptance_policy.py" \
  "$SCRIPT_DIR/acceptance_checkpoints.py" "$SCRIPT_DIR/test_acceptance_checkpoints.py"
python3 "$SCRIPT_DIR/test_acceptance_policy.py"
python3 "$SCRIPT_DIR/test_acceptance_checkpoints.py"
python3 -m json.tool "$SCRIPT_DIR/acceptance-policy.json" >/dev/null
grep -q -- '--cold' "$SCRIPT_DIR/extended-checks.sh"
grep -q -- '--confirm-cold-rebuild TRUNCATE_DOCUMENT_SEARCH_INDEX' "$SCRIPT_DIR/extended-checks.sh"
grep -q 'acceptance_policy.py' "$SCRIPT_DIR/extended-checks.sh"
grep -q 'acceptance_checkpoints.py' "$SCRIPT_DIR/acceptance-cycle.sh"

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
  */health/) echo "${CI_HEALTH_BODY:-healthy}" ;;
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
APP_HEALTH_EXPECT_REGEX='^(ok|healthy|ready)$'
SEARCH_REINDEX_DOCUMENTS=10000
SEARCH_REINDEX_MAX_SECONDS=3600
MINIO_HASH_SAMPLE_SIZE=500
ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX=YES
ACCEPTANCE_EVIDENCE_ROOT=$tmp/evidence
ACCEPTANCE_CHANGE_ID=CI-P1
ACCEPTANCE_OPERATOR=ci
ACCEPTANCE_SITE=ci-stand
EOF

export PATH="$tmp/bin:$PATH"
export ACCEPTANCE_ENV="$tmp/acceptance.env"

record_required_checkpoints() {
  local run_id="$1" name
  for name in \
    planned-switchover-complete \
    unplanned-failover-complete \
    pitr-validated \
    replica-rebuild-validated \
    minio-failover-validated \
    minio-failback-validated \
    alertmanager-peer-loss-validated \
    application-smoke-validated \
    write-path-switchover-measured; do
    bash "$SCRIPT_DIR/acceptance-cycle.sh" "$run_id" checkpoint "$name" >/dev/null
  done
}

write_passing_extended_results() {
  local run_id="$1" docs="$2" seconds="$3" sample="$4" seed="$5"
  cat >"$tmp/evidence/$run_id/extended-results.env" <<EOF
search_reindex_required_documents=$docs
search_reindex_documents=$docs
search_reindex_seconds=$seconds
search_reindex_limit_seconds=3600
search_reindex_mode=cold
search_reindex_result=PASS
queue_worker_kill_result=PASS
queue_redis_kill_result=PASS
minio_hash_required_count=$sample
minio_hash_sample_count=$sample
minio_hash_mismatches=0
minio_hash_seed=$seed
minio_hash_algorithm=sorted-random-v1
minio_hash_result=PASS
EOF
}

bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 preflight >/dev/null
record_required_checkpoints ci-stage4
write_passing_extended_results ci-stage4 10000 3599 500 ci-stage4

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
grep -q 'Acceptance policy: \*\*PASS\*\*' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Required checkpoints: \*\*PASS\*\*' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'observed_rpo_seconds: 10' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'observed_rto_seconds: 180' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Search rebuild mode: cold' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'MinIO sample seed: ci-stage4' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'MinIO sample algorithm: sorted-random-v1' "$tmp/evidence/ci-stage4/RESULT.md"

# Missing manual evidence must fail even if all declared result flags and
# extended measurements say PASS.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-no-checkpoints preflight >/dev/null
write_passing_extended_results ci-stage4-no-checkpoints 10000 100 500 ci-stage4-no-checkpoints
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T19:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T18:59:50Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T19:03:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-no-checkpoints finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted run without required real-stand checkpoints' >&2
  exit 1
fi
grep -q 'DISCREPANCY: required real-stand checkpoints are incomplete' \
  "$tmp/evidence/ci-stage4-no-checkpoints/RESULT.md"

# Reindex over the 60-minute baseline remains a hard discrepancy without waiver.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-slow preflight >/dev/null
record_required_checkpoints ci-stage4-slow
cat >"$tmp/evidence/ci-stage4-slow/extended-results.env" <<'EOF'
search_reindex_required_documents=10000
search_reindex_documents=10000
search_reindex_seconds=3601
search_reindex_limit_seconds=3600
search_reindex_mode=cold
search_reindex_result=FAIL
queue_worker_kill_result=PASS
queue_redis_kill_result=PASS
minio_hash_required_count=500
minio_hash_sample_count=500
minio_hash_mismatches=0
minio_hash_seed=ci-stage4-slow
minio_hash_algorithm=sorted-random-v1
minio_hash_result=PASS
EOF
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T20:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T19:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T20:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-slow finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted cold reindex > 60 minutes' >&2
  exit 1
fi
grep -q 'DISCREPANCY: cold reindex took 3601s' "$tmp/evidence/ci-stage4-slow/RESULT.md"

# Any missing extended check must fail closed, independently of checkpoints.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-missing preflight >/dev/null
record_required_checkpoints ci-stage4-missing
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T21:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T20:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T21:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-missing finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted NOT_RUN extended checks' >&2
  exit 1
fi

# A failing tool must tell the operator why instead of dying silently under set -e.
mkdir -p "$tmp/badbin"
cp "$tmp/bin/patronictl" "$tmp/bin/pgbackrest" "$tmp/bin/curl" "$tmp/badbin/"
cat >"$tmp/badbin/etcdctl" <<'EOF'
#!/usr/bin/env bash
echo 'Error: open /etc/bz-get/pki/ca.crt: no such file or directory' >&2
exit 1
EOF
chmod +x "$tmp/badbin/etcdctl"
etcd_err="$tmp/etcd-failure.txt"
if PATH="$tmp/badbin:$PATH" bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-etcd-down preflight \
    >"$tmp/etcd-failure.out" 2>"$etcd_err"; then
  echo 'ERROR: preflight succeeded with a failing etcdctl' >&2
  exit 1
fi
grep -q 'etcdctl endpoint health failed' "$etcd_err"
grep -q 'no such file or directory' "$etcd_err"

if CI_HEALTH_BODY=unhealthy bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-sick preflight >/dev/null 2>&1; then
  echo 'ERROR: preflight accepted an unhealthy application health response' >&2
  exit 1
fi

# Weakening the version-controlled baseline without a waiver must fail before
# stand work starts; editing only acceptance.env cannot silently redefine PASS.
cp "$tmp/acceptance.env" "$tmp/small-no-waiver.env"
cat >>"$tmp/small-no-waiver.env" <<'EOF'
SEARCH_REINDEX_DOCUMENTS=5000
MINIO_HASH_SAMPLE_SIZE=120
EOF
if ACCEPTANCE_ENV="$tmp/small-no-waiver.env" \
    bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-small-no-waiver preflight \
    >"$tmp/small-no-waiver.out" 2>"$tmp/small-no-waiver.err"; then
  echo 'ERROR: preflight accepted weakened criteria without waiver' >&2
  exit 1
fi
grep -q 'violate repository baseline' "$tmp/small-no-waiver.err"

# Bypassing preflight does not bypass policy: the drill wrapper itself must fail.
if ACCEPTANCE_ENV="$tmp/small-no-waiver.env" \
    bash "$SCRIPT_DIR/extended-checks.sh" ci-direct-policy queue-start worker \
    >"$tmp/direct-policy.out" 2>"$tmp/direct-policy.err"; then
  echo 'ERROR: direct extended-check bypassed acceptance policy' >&2
  exit 1
fi

# The same weaker criteria are allowed only with complete approval metadata and
# must be reported as PASS_WITH_WAIVER, never as an unqualified PASS.
cp "$tmp/acceptance.env" "$tmp/small-waiver.env"
cat >>"$tmp/small-waiver.env" <<'EOF'
SEARCH_REINDEX_DOCUMENTS=5000
MINIO_HASH_SAMPLE_SIZE=120
ACCEPTANCE_WAIVER_ID=WAIVER-CI-17
ACCEPTANCE_WAIVER_APPROVER=customer-ci
ACCEPTANCE_WAIVER_REASON='reduced CI stand dataset'
EOF
ACCEPTANCE_ENV="$tmp/small-waiver.env" \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-small-waiver preflight >/dev/null
ACCEPTANCE_ENV="$tmp/small-waiver.env" record_required_checkpoints ci-stage4-small-waiver
write_passing_extended_results ci-stage4-small-waiver 5000 120 120 ci-waiver-seed
env \
  ACCEPTANCE_ENV="$tmp/small-waiver.env" \
  ACCEPTANCE_DB_RESULT=PASS ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T22:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T21:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T22:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-small-waiver finalize >/dev/null
grep -q 'Overall: \*\*PASS_WITH_WAIVER\*\*' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Acceptance policy: \*\*PASS_WITH_WAIVER\*\*' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Waiver ID: WAIVER-CI-17' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Waiver approver: customer-ci' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Waiver reason: reduced CI stand dataset' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'search_reindex_documents_min' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'minio_version_sample_min' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"

# A run that does not meet even its declared effective criteria still fails.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-short preflight >/dev/null
record_required_checkpoints ci-stage4-short
cat >"$tmp/evidence/ci-stage4-short/extended-results.env" <<'EOF'
search_reindex_required_documents=10000
search_reindex_documents=5000
search_reindex_seconds=120
search_reindex_limit_seconds=3600
search_reindex_mode=cold
search_reindex_result=PASS
queue_worker_kill_result=PASS
queue_redis_kill_result=PASS
minio_hash_required_count=500
minio_hash_sample_count=120
minio_hash_mismatches=0
minio_hash_seed=ci-stage4-short
minio_hash_algorithm=sorted-random-v1
minio_hash_result=PASS
EOF
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T23:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T22:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T23:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-short finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted measurements short of the declared criteria' >&2
  exit 1
fi
grep -q 'DISCREPANCY: cold reindex covered 5000 documents instead of the required 10000' "$tmp/evidence/ci-stage4-short/RESULT.md"
grep -q 'DISCREPANCY: MinIO versioned sample covered 120 versions instead of the required 500' "$tmp/evidence/ci-stage4-short/RESULT.md"

echo 'Stage 4 acceptance harness validation passed.'
