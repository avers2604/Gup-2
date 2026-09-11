#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ACCEPTANCE_ENV="${ACCEPTANCE_ENV:-/etc/bz-get/stage4-acceptance.env}"

usage() {
  cat <<'EOF'
Usage:
  extended-checks.sh RUN_ID cold-reindex
  extended-checks.sh RUN_ID queue-start worker|redis
  extended-checks.sh RUN_ID queue-verify worker|redis
  extended-checks.sh RUN_ID minio-hash          (alias: minio-hash-500)

Acceptance minima/maxima are version-controlled in acceptance-policy.json.
Stricter stand values are allowed. Weaker values require a complete
ACCEPTANCE_WAIVER_ID / APPROVER / REASON and are classified PASS_WITH_WAIVER.
EOF
}

run_id="${1:-}"
action="${2:-}"
subject="${3:-}"
[[ "$run_id" =~ ^[A-Za-z0-9._-]+$ ]] || { usage >&2; exit 64; }
[[ -r "$ACCEPTANCE_ENV" ]] || { echo "ERROR: unreadable $ACCEPTANCE_ENV" >&2; exit 66; }
set -a
# shellcheck disable=SC1090
. "$ACCEPTANCE_ENV"
set +a

: "${ACCEPTANCE_EVIDENCE_ROOT:?ACCEPTANCE_EVIDENCE_ROOT is required}"
run_dir="$ACCEPTANCE_EVIDENCE_ROOT/$run_id"
mkdir -p "$run_dir"
results="$run_dir/extended-results.env"
policy_evidence="$run_dir/acceptance-policy.json"

# Prevent an operator from silently weakening acceptance by editing only the
# host-local inventory. The repository policy is the baseline of record.
python3 "$SCRIPT_DIR/acceptance_policy.py" --evidence "$policy_evidence" >/dev/null

ensure_results() {
  [[ -f "$results" ]] && return
  cat >"$results" <<EOF
search_reindex_required_documents=${SEARCH_REINDEX_DOCUMENTS:-10000}
search_reindex_documents=0
search_reindex_seconds=0
search_reindex_limit_seconds=${SEARCH_REINDEX_MAX_SECONDS:-3600}
search_reindex_mode=NOT_RUN
search_reindex_result=NOT_RUN
queue_worker_kill_result=NOT_RUN
queue_redis_kill_result=NOT_RUN
minio_hash_required_count=${MINIO_HASH_SAMPLE_SIZE:-500}
minio_hash_sample_count=0
minio_hash_mismatches=0
minio_hash_result=NOT_RUN
EOF
}

set_result() {
  local key="$1" value="$2"
  python3 - "$results" "$key" "$value" <<'PY'
import sys
from pathlib import Path
path, key, value = sys.argv[1:]
rows = {}
for line in Path(path).read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        rows[k] = v
rows[key] = value
Path(path).write_text("".join(f"{k}={v}\n" for k, v in rows.items()))
PY
}

checkpoint() {
  ACCEPTANCE_ENV="$ACCEPTANCE_ENV" bash "$SCRIPT_DIR/acceptance-cycle.sh" "$run_id" checkpoint "$1" >/dev/null
}

ensure_results

case "$action" in
  cold-reindex)
    manage="${ACCEPTANCE_MANAGE_COMMAND:-python manage.py}"
    count="${SEARCH_REINDEX_DOCUMENTS:-10000}"
    limit="${SEARCH_REINDEX_MAX_SECONDS:-3600}"
    batch="${SEARCH_REINDEX_BATCH_SIZE:-1000}"
    out="$run_dir/search-reindex.json"
    err="$run_dir/search-reindex.stderr"
    set_result search_reindex_required_documents "$count"
    set_result search_reindex_limit_seconds "$limit"
    set +e
    bash -lc "cd '$REPO_ROOT' && $manage rebuild_search_index --cold --batch-size '$batch' --require-count '$count' --max-seconds '$limit' --json" >"$out" 2>"$err"
    rc=$?
    set -e
    python3 - "$out" "$results" <<'PY'
import json
import sys
from pathlib import Path
out, results = map(Path, sys.argv[1:])
lines = [line for line in out.read_text().splitlines() if line.strip().startswith("{")]
if not lines:
    raise SystemExit("cold reindex command produced no JSON measurement")
p = json.loads(lines[-1])
if p.get("mode") != "cold":
    raise SystemExit(f"acceptance requires mode=cold, got {p.get('mode')!r}")
rows = {}
for line in results.read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        rows[k] = v
rows.update({
    "search_reindex_mode": str(p["mode"]),
    "search_reindex_documents": str(p["documents"]),
    "search_reindex_seconds": str(p["elapsed_seconds"]),
    "search_reindex_limit_seconds": str(p["max_seconds"]),
    "search_reindex_result": str(p["result"]),
})
results.write_text("".join(f"{k}={v}\n" for k, v in rows.items()))
PY
    if (( rc == 0 )); then
      checkpoint "cold-reindex-${count}-passed"
      echo "Cold reindex PASS; measurement=$out"
    else
      set_result search_reindex_result FAIL
      checkpoint "cold-reindex-${count}-failed"
      echo "Cold reindex FAILED; measurement=$out stderr=$err" >&2
      exit 1
    fi
    ;;

  queue-start)
    [[ "$subject" == worker || "$subject" == redis ]] || { usage >&2; exit 64; }
    manage="${ACCEPTANCE_MANAGE_COMMAND:-python manage.py}"
    count="${QUEUE_DRILL_TASKS:-20}"
    sleep_seconds="${QUEUE_DRILL_SLEEP_SECONDS:-20}"
    probe_run="${run_id}-${subject}"
    bash -lc "cd '$REPO_ROOT' && $manage queue_drill start --run-id '$probe_run' --count '$count' --sleep-seconds '$sleep_seconds'" \
      | tee "$run_dir/queue-${subject}-start.txt"
    checkpoint "queue-${subject}-kill-started"
    echo "Inject kill -9 for $subject now, recover the component, then run queue-verify $subject."
    ;;

  queue-verify)
    [[ "$subject" == worker || "$subject" == redis ]] || { usage >&2; exit 64; }
    manage="${ACCEPTANCE_MANAGE_COMMAND:-python manage.py}"
    count="${QUEUE_DRILL_TASKS:-20}"
    probe_run="${run_id}-${subject}"
    set +e
    bash -lc "cd '$REPO_ROOT' && $manage queue_drill verify --run-id '$probe_run' --expected-count '$count'" \
      >"$run_dir/queue-${subject}-verify.txt" 2>&1
    rc=$?
    set -e
    if (( rc == 0 )); then
      set_result "queue_${subject}_kill_result" PASS
      checkpoint "queue-${subject}-kill-passed"
      cat "$run_dir/queue-${subject}-verify.txt"
    else
      set_result "queue_${subject}_kill_result" FAIL
      checkpoint "queue-${subject}-kill-failed"
      cat "$run_dir/queue-${subject}-verify.txt" >&2
      exit 1
    fi
    ;;

  minio-hash-500|minio-hash)
    # Resolve before MINIO_DR_ENV is sourced: the acceptance inventory owns the
    # required sample and the MinIO helper env may not silently override it.
    sample_size="${MINIO_HASH_SAMPLE_SIZE:-500}"
    [[ "$sample_size" =~ ^[0-9]+$ ]] && (( sample_size > 0 )) || {
      echo "ERROR: MINIO_HASH_SAMPLE_SIZE must be a positive integer" >&2
      exit 64
    }
    set_result minio_hash_required_count "$sample_size"
    : "${MINIO_DR_ENV:?MINIO_DR_ENV is required}"
    [[ -r "$MINIO_DR_ENV" ]] || { echo "ERROR: unreadable $MINIO_DR_ENV" >&2; exit 66; }
    set -a
    # shellcheck disable=SC1090
    . "$MINIO_DR_ENV"
    set +a
    evidence="$run_dir/minio-versioned-sample.csv"
    set +e
    MINIO_HASH_SAMPLE_SIZE="$sample_size" \
    MINIO_HASH_SAMPLE_SEED="$run_id" \
    MINIO_HASH_EVIDENCE_FILE="$evidence" \
      bash "$REPO_ROOT/deploy/minio/dr/verify-500-hashes.sh" \
      >"$run_dir/minio-versioned-sample.txt" 2>&1
    rc=$?
    set -e
    checked=0
    mismatches=0
    if [[ -f "$evidence" ]]; then
      checked=$(( $(wc -l <"$evidence") - 1 ))
      mismatches="$(grep -c ',FAIL$' "$evidence" || true)"
    fi
    set_result minio_hash_sample_count "$checked"
    set_result minio_hash_mismatches "$mismatches"
    if (( rc == 0 && checked == sample_size && mismatches == 0 )); then
      set_result minio_hash_result PASS
      checkpoint "minio-versioned-${sample_size}-passed"
      cat "$run_dir/minio-versioned-sample.txt"
    else
      set_result minio_hash_result FAIL
      checkpoint "minio-versioned-${sample_size}-failed"
      cat "$run_dir/minio-versioned-sample.txt" >&2
      exit 1
    fi
    ;;

  *)
    usage >&2
    exit 64
    ;;
esac
