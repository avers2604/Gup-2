#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

bash -n "$SCRIPT_DIR/patroni-pgbackrest-restore.sh"
bash -n "$SCRIPT_DIR/rebuild-replica.sh"
bash -n "$SCRIPT_DIR/combined-drill.sh"
bash -n "$SCRIPT_DIR/approve-pgbackrest-rebuild.sh"

grep -q 'create_replica_methods:' "$REPO_ROOT/deploy/ha/patroni.yml.example"
grep -q 'patroni-pgbackrest-restore.sh' "$REPO_ROOT/deploy/ha/patroni.yml.example"
grep -q 'manual-pitr-approved' "$SCRIPT_DIR/patroni-pgbackrest-restore.sh"
grep -q 'status=approved' "$SCRIPT_DIR/patroni-pgbackrest-restore.sh"
grep -q -- '--execute' "$SCRIPT_DIR/rebuild-replica.sh"
if grep -q -- '--target-action=promote' "$SCRIPT_DIR/patroni-pgbackrest-restore.sh"; then
  echo 'ERROR: replica restore wrapper must never promote a restored node' >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/data" "$tmp/evidence"
touch "$tmp/patroni.yml"
printf 'status=approved\nmanual_pitr_drill_id=ci\n' >"$tmp/manual-pitr-approved"

cat >"$tmp/bin/patronictl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == "reinit" ]]; then
    printf '%s\n' "$*" >>"${FAKE_REINIT_LOG:?}"
    echo 'Success: reinitialize requested'
    exit 0
  fi
done
cat <<'JSON'
[
  {"Member":"db1","Role":"Leader","State":"running"},
  {"Member":"db2","Role":"Replica","State":"streaming"}
]
JSON
EOF
chmod +x "$tmp/bin/patronictl"

cat >"$tmp/bin/pgbackrest" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  if [[ "$arg" == "info" || "$arg" == "--output=json" ]]; then
    cat <<'JSON'
[{"name":"bz-get","status":{"code":0},"backup":[{"label":"20260910-190000F"}]}]
JSON
    exit 0
  fi
done
printf '%s\n' "$*" >>"${FAKE_RESTORE_LOG:?}"
exit 0
EOF
chmod +x "$tmp/bin/pgbackrest"

export PATH="$tmp/bin:$PATH"
export FAKE_REINIT_LOG="$tmp/reinit.log"
export FAKE_RESTORE_LOG="$tmp/restore.log"
: >"$FAKE_REINIT_LOG"
: >"$FAKE_RESTORE_LOG"

common_env=(
  PATRONI_CONFIG="$tmp/patroni.yml"
  PATRONI_SCOPE=bz-get
  PATRONI_PGBACKREST_APPROVAL_FILE="$tmp/manual-pitr-approved"
  PGBACKREST_BIN="$tmp/bin/pgbackrest"
)

env "${common_env[@]}" "$SCRIPT_DIR/rebuild-replica.sh" db2 >/dev/null
if [[ -s "$FAKE_REINIT_LOG" ]]; then
  echo 'ERROR: dry-run executed patronictl reinit' >&2
  exit 1
fi

env "${common_env[@]}" "$SCRIPT_DIR/rebuild-replica.sh" db2 --execute >/dev/null
grep -q 'reinit bz-get db2 --wait --force' "$FAKE_REINIT_LOG"

if env "${common_env[@]}" "$SCRIPT_DIR/rebuild-replica.sh" db1 --execute >/dev/null 2>&1; then
  echo 'ERROR: leader rebuild was not rejected' >&2
  exit 1
fi

env \
  PATRONI_PGBACKREST_APPROVAL_FILE="$tmp/manual-pitr-approved" \
  PATRONI_ALLOWED_DATA_ROOT="$tmp" \
  PATRONI_EXPECTED_SCOPE=bz-get \
  PGBACKREST_BIN="$tmp/bin/pgbackrest" \
  FAKE_RESTORE_LOG="$FAKE_RESTORE_LOG" \
  "$SCRIPT_DIR/patroni-pgbackrest-restore.sh" \
  --scope=bz-get --datadir="$tmp/data" --role=replica --connstring='host=db1' >/dev/null

grep -q -- '--stanza=bz-get' "$FAKE_RESTORE_LOG"
grep -q -- '--pg1-path=' "$FAKE_RESTORE_LOG"
grep -q -- '--delta' "$FAKE_RESTORE_LOG"
if grep -q -- '--target-action=promote' "$FAKE_RESTORE_LOG"; then
  echo 'ERROR: fake restore received target-action=promote' >&2
  exit 1
fi

printf 'status=pending\n' >"$tmp/unapproved"
if env \
  PATRONI_PGBACKREST_APPROVAL_FILE="$tmp/unapproved" \
  PATRONI_ALLOWED_DATA_ROOT="$tmp" \
  PATRONI_EXPECTED_SCOPE=bz-get \
  PGBACKREST_BIN="$tmp/bin/pgbackrest" \
  "$SCRIPT_DIR/patroni-pgbackrest-restore.sh" \
  --scope=bz-get --datadir="$tmp/data" --role=replica >/dev/null 2>&1; then
  echo 'ERROR: restore wrapper ran with an unapproved marker' >&2
  exit 1
fi

env \
  DRILL_ID=ci-part4 \
  DRILL_EVIDENCE_ROOT="$tmp/evidence" \
  PATRONI_CONFIG="$tmp/patroni.yml" \
  PATRONI_SCOPE=bz-get \
  PGBACKREST_BIN="$tmp/bin/pgbackrest" \
  MINIO_DR_ENV="$tmp/missing-minio.env" \
  ALERTMANAGER_URL='' \
  "$SCRIPT_DIR/combined-drill.sh" preflight >/dev/null

env \
  DRILL_ID=ci-part4 \
  DRILL_EVIDENCE_ROOT="$tmp/evidence" \
  PATRONI_CONFIG="$tmp/patroni.yml" \
  PATRONI_SCOPE=bz-get \
  PGBACKREST_BIN="$tmp/bin/pgbackrest" \
  MINIO_DR_ENV="$tmp/missing-minio.env" \
  ALERTMANAGER_URL='' \
  "$SCRIPT_DIR/combined-drill.sh" checkpoint failover-complete >/dev/null

env \
  DRILL_ID=ci-part4 \
  DRILL_EVIDENCE_ROOT="$tmp/evidence" \
  PATRONI_CONFIG="$tmp/patroni.yml" \
  PATRONI_SCOPE=bz-get \
  PGBACKREST_BIN="$tmp/bin/pgbackrest" \
  MINIO_DR_ENV="$tmp/missing-minio.env" \
  ALERTMANAGER_URL='' \
  DRILL_INCIDENT_UTC=2026-09-10T19:00:00Z \
  DRILL_LAST_DURABLE_UTC=2026-09-10T18:59:50Z \
  DRILL_SERVICE_RESTORED_UTC=2026-09-10T19:03:00Z \
  "$SCRIPT_DIR/combined-drill.sh" finish >/dev/null

grep -q 'observed_rpo_upper_bound_seconds: 10' "$tmp/evidence/ci-part4/RESULT.md"
grep -q 'observed_rto_seconds: 180' "$tmp/evidence/ci-part4/RESULT.md"
grep -q 'failover-complete' "$tmp/evidence/ci-part4/checkpoints.csv"

# Approval helper must refuse TBD and accept an explicitly signed PASS in
# dry-run mode. Root-owned marker creation is intentionally not exercised in CI.
if env DRILL_EVIDENCE_ROOT="$tmp/evidence" "$SCRIPT_DIR/approve-pgbackrest-rebuild.sh" ci-part4 ci-reviewer >/dev/null 2>&1; then
  echo 'ERROR: approval helper accepted an unsigned/TBD drill' >&2
  exit 1
fi
printf '\nPASS/FAIL: PASS\n' >>"$tmp/evidence/ci-part4/RESULT.md"
env DRILL_EVIDENCE_ROOT="$tmp/evidence" "$SCRIPT_DIR/approve-pgbackrest-rebuild.sh" ci-part4 ci-reviewer >/dev/null

echo 'Stage 4 part 4 rebuild/drill validation passed.'
