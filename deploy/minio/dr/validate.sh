#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

bash -n configure-replication.sh
bash -n check-replication.sh
bash -n verify-500-hashes.sh
python3 -m py_compile verify_versioned_sample.py test_verify_versioned_sample.py
python3 test_verify_versioned_sample.py
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

# Acceptance wrapper must delegate to the version-aware verifier; a legacy
# current-object-only `mc cat` comparison would miss VersionId/retention/hold drift.
grep -q 'verify_versioned_sample.py' verify-500-hashes.sh
grep -q 'MINIO_HASH_SAMPLE_SIZE:-500' verify-500-hashes.sh
grep -q 'list_object_versions' verify_versioned_sample.py
grep -q 'get_object_retention' verify_versioned_sample.py
grep -q 'get_object_legal_hold' verify_versioned_sample.py

echo "MinIO DR static validation passed."
