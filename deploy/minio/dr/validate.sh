#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

bash -n configure-replication.sh
bash -n check-replication.sh
python3 -m json.tool replication-admin-policy.json >/dev/null
python3 -m json.tool replication-target-policy.json >/dev/null

# Guard the production reference against accidentally weakening the WORM copy.
grep -q 'mc mb --with-lock' README.md
grep -q 'retention info --default' configure-replication.sh

# Replication credentials must remain least-privileged: the bootstrap checks
# Versioning but does not grant itself permission to change bucket versioning.
if grep -q 'mc_cmd version enable' configure-replication.sh; then
  echo 'ERROR: replication bootstrap must not mutate Versioning policy' >&2
  exit 1
fi

# Validate the permissions required by MinIO bucket replication, including
# version-level deletes and Object Lock metadata. This catches syntactically
# valid JSON policies that would fail only under real WORM traffic.
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
    "admin:SetBucketTarget",
    "admin:GetBucketTarget",
    "s3:GetReplicationConfiguration",
    "s3:PutReplicationConfiguration",
    "s3:ListBucket",
    "s3:ListBucketVersions",
    "s3:GetBucketVersioning",
    "s3:GetObjectRetention",
    "s3:GetObjectLegalHold",
}
required_target = {
    "s3:GetReplicationConfiguration",
    "s3:ListBucket",
    "s3:ListBucketVersions",
    "s3:GetBucketVersioning",
    "s3:GetBucketObjectLockConfiguration",
    "s3:PutBucketObjectLockConfiguration",
    "s3:ReplicateTags",
    "s3:PutObjectRetention",
    "s3:PutObjectLegalHold",
    "s3:DeleteObjectVersion",
    "s3:ReplicateObject",
    "s3:ReplicateDelete",
}

for name, present, required in (
    ("source", source, required_source),
    ("target", target, required_target),
):
    missing = sorted(required - present)
    if missing:
        raise SystemExit(f"{name} replication policy missing actions: {', '.join(missing)}")
PY

# Guard against accidentally committing example credentials as if they were real.
grep -q 'CHANGE_ME_REPLICATION_ADMIN_ACCESS_KEY' dr.env.example
grep -q 'CHANGE_ME_REPLICATION_TARGET_ACCESS_KEY' dr.env.example

echo "MinIO DR static validation passed."
