#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$SCRIPT_DIR/acceptance-cycle.sh"
bash -n "$SCRIPT_DIR/extended-checks.sh"
python3 -m py_compile "$SCRIPT_DIR/acceptance_policy.py" "$SCRIPT_DIR/test_acceptance_policy.py"
python3 "$SCRIPT_DIR/test_acceptance_policy.py"
python3 -m json.tool "$SCRIPT_DIR/acceptance-policy.json" >/dev/null
grep -q -- '--cold' "$SCRIPT_DIR/extended-checks.sh"
grep -q 'acceptance_policy.py' "$SCRIPT_DIR/extended-checks.sh"

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
ACCEPTANCE_EVIDENCE_ROOT=$tmp/evidence
ACCEPTANCE_CHANGE_ID=CI-P1
ACCEPTANCE_OPERATOR=ci
ACCEPTANCE_SITE=ci-stand
EOF

export PATH="$tmp/bin:$PATH"
export ACCEPTANCE_ENV="$tmp/acceptance.env"

bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 preflight >/dev/null
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 checkpoint planned-switchover-complete >/dev/null
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4 checkpoint pitr-validated >/dev/null

cat >"$tmp/evidence/ci-stage4/extended-results.env" <<'EOF'
search_reindex_required_documents=10000
search_reindex_documents=10000
search_reindex_seconds=3599
search_reindex_limit_seconds=3600
search_reindex_mode=cold
search_reindex_result=PASS
queue_worker_kill_result=PASS
queue_redis_kill_result=PASS
minio_hash_required_count=500
minio_hash_sample_count=500
minio_hash_mismatches=0
minio_hash_result=PASS
EOF

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
grep -q 'observed_rpo_seconds: 10' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'observed_rto_seconds: 180' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Search rebuild mode: cold' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Cold search reindex documents: 10000' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Cold search reindex seconds: 3599' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'MinIO versioned WORM sample versions: 500' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'MinIO versioned WORM required versions: 500' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Celery worker kill -9 / redelivery: \*\*PASS\*\*' "$tmp/evidence/ci-stage4/RESULT.md"
grep -q 'Redis kill -9 / recovery: \*\*PASS\*\*' "$tmp/evidence/ci-stage4/RESULT.md"

# Reindex over the 60-minute baseline remains a hard discrepancy without waiver.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-slow preflight >/dev/null
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
minio_hash_result=PASS
EOF
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T20:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T19:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T20:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-slow finalize >/dev/null 2>&1; then
  echo 'ERROR: finalize accepted cold reindex > 60 minutes' >&2
  exit 1
fi
grep -q 'DISCREPANCY: cold reindex took 3601s' "$tmp/evidence/ci-stage4-slow/RESULT.md"

# Any missing extended check must fail closed.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-missing preflight >/dev/null
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
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

# 'unhealthy' must not satisfy an anchored expectation of 'healthy'.
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
cat >"$tmp/evidence/ci-stage4-small-waiver/extended-results.env" <<'EOF'
search_reindex_required_documents=5000
search_reindex_documents=5000
search_reindex_seconds=120
search_reindex_limit_seconds=3600
search_reindex_mode=cold
search_reindex_result=PASS
queue_worker_kill_result=PASS
queue_redis_kill_result=PASS
minio_hash_required_count=120
minio_hash_sample_count=120
minio_hash_mismatches=0
minio_hash_result=PASS
EOF
env \
  ACCEPTANCE_ENV="$tmp/small-waiver.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
  ACCEPTANCE_INCIDENT_UTC=2026-09-10T22:00:00Z \
  ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T21:59:55Z \
  ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T22:01:00Z \
  bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-small-waiver finalize >/dev/null
grep -q 'Overall: \*\*PASS_WITH_WAIVER\*\*' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Acceptance policy: \*\*PASS_WITH_WAIVER\*\*' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'Waiver ID: WAIVER-CI-17' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'search_reindex_documents_min' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"
grep -q 'minio_version_sample_min' "$tmp/evidence/ci-stage4-small-waiver/RESULT.md"

# A run that does not meet even its declared effective criteria still fails,
# regardless of baseline/waiver semantics.
bash "$SCRIPT_DIR/acceptance-cycle.sh" ci-stage4-short preflight >/dev/null
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
minio_hash_result=PASS
EOF
if env \
  ACCEPTANCE_ENV="$tmp/acceptance.env" \
  ACCEPTANCE_DB_RESULT=PASS \
  ACCEPTANCE_MINIO_RESULT=PASS \
  ACCEPTANCE_ALERT_RESULT=PASS \
  ACCEPTANCE_APP_RESULT=PASS \
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
