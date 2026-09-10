#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

bash -n configure-replication.sh
bash -n check-replication.sh
python3 -m json.tool replication-admin-policy.json >/dev/null
python3 -m json.tool replication-target-policy.json >/dev/null

# Guard the production reference against accidentally weakening the WORM copy.
grep -q 'with Object Lock' README.md
grep -q 'retention info' configure-replication.sh

# Guard against accidentally committing example credentials as if they were real.
grep -q 'CHANGE_ME_REPLICATION_ADMIN_ACCESS_KEY' dr.env.example
grep -q 'CHANGE_ME_REPLICATION_TARGET_ACCESS_KEY' dr.env.example

echo "MinIO DR static validation passed."
