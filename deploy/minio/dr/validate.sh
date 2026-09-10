#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

bash -n configure-replication.sh
bash -n check-replication.sh
bash -n verify-500-hashes.sh
python3 -m json.tool replication-admin-policy.json >/dev/null
python3 -m json.tool replication-target-policy.json >/dev/null

grep -q 'mc mb --with-lock' README.md
grep -q 'retention info --default' configure-replication.sh

if grep -q 'mc_cmd version enable' configure-replication.sh; then
  echo 'ERROR: replication bootstrap must not mutate Versioning policy' >&2
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path


def actions(path: str) -> set[str]:
    data = json.loads(Path(path).read_text())
    result: set[str] = set()
    for statement in data.get("Statement", []):
        value = statement.get("Action", [])
        if isinstance(value, str):
            result.add(value)
        else:
            result.update(value)
    return result

source = actions("replication-admin-policy.json")
target = actions("replication-target-policy.json")
required_source = {
    "admin:SetBucketTarget", "admin:GetBucketTarget", "s3:GetReplicationConfiguration",
    "s3:PutReplicationConfiguration", "s3:ListBucket", "s3:ListBucketVersions",
    "s3:GetBucketVersioning", "s3:GetObjectRetention", "s3:GetObjectLegalHold",
}
required_target = {
    "s3:GetReplicationConfiguration", "s3:ListBucket", "s3:ListBucketVersions",
    "s3:GetBucketVersioning", "s3:GetBucketObjectLockConfiguration",
    "s3:PutBucketObjectLockConfiguration", "s3:ReplicateTags", "s3:PutObjectRetention",
    "s3:PutObjectLegalHold", "s3:DeleteObjectVersion", "s3:ReplicateObject", "s3:ReplicateDelete",
}
for name, present, required in (("source", source, required_source), ("target", target, required_target)):
    missing = sorted(required - present)
    if missing:
        raise SystemExit(f"{name} replication policy missing actions: {', '.join(missing)}")
PY

grep -q 'CHANGE_ME_REPLICATION_ADMIN_ACCESS_KEY' dr.env.example
grep -q 'CHANGE_ME_REPLICATION_TARGET_ACCESS_KEY' dr.env.example

# Functional smoke-test of the hash comparer with a fake mc. The real
# acceptance wrapper passes MINIO_HASH_SAMPLE_SIZE (default 500); using 7 keeps CI
# fast while still exercising random selection, download and SHA comparison.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat >"$tmp/mc" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  alias|ready) exit 0 ;;
  ls)
    for i in $(seq 1 10); do
      printf '{"type":"file","key":"file-%04d.bin"}\n' "$i"
    done
    ;;
  cat)
    path="${2:-}"
    printf 'payload-%s\n' "${path##*/}"
    ;;
  *) echo "fake mc: unsupported: $*" >&2; exit 2 ;;
esac
EOF
chmod +x "$tmp/mc"

MINIO_SOURCE_URL=http://source \
MINIO_TARGET_URL=http://target \
MINIO_SOURCE_ACCESS_KEY=source \
MINIO_SOURCE_SECRET_KEY=source-secret \
MINIO_TARGET_ACCESS_KEY=target \
MINIO_TARGET_SECRET_KEY=target-secret \
MINIO_BUCKET_ORIGINALS=originals \
MC_BIN="$tmp/mc" \
MINIO_HASH_SAMPLE_SIZE=7 \
MINIO_HASH_EVIDENCE_FILE="$tmp/hashes.csv" \
  bash verify-500-hashes.sh >/dev/null

[[ "$(( $(wc -l <"$tmp/hashes.csv") - 1 ))" -eq 7 ]]
grep -q 'MINIO_HASH_SAMPLE_SIZE:-500' verify-500-hashes.sh

echo "MinIO DR static validation passed."
