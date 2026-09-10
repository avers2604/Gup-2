#!/usr/bin/env bash
# Configure one-way active -> passive MinIO bucket replication for the two
# application buckets. Run only after both sites and destination buckets are
# provisioned. This script never enables reverse replication automatically.
set -euo pipefail

: "${MINIO_SOURCE_URL:?set MINIO_SOURCE_URL}"
: "${MINIO_TARGET_URL:?set MINIO_TARGET_URL}"
: "${MINIO_SOURCE_ACCESS_KEY:?set MINIO_SOURCE_ACCESS_KEY}"
: "${MINIO_SOURCE_SECRET_KEY:?set MINIO_SOURCE_SECRET_KEY}"
: "${MINIO_TARGET_ACCESS_KEY:?set MINIO_TARGET_ACCESS_KEY}"
: "${MINIO_TARGET_SECRET_KEY:?set MINIO_TARGET_SECRET_KEY}"
: "${MINIO_BUCKET_ORIGINALS:?set MINIO_BUCKET_ORIGINALS}"
: "${MINIO_BUCKET_WORKING:?set MINIO_BUCKET_WORKING}"

MC_BIN="${MC_BIN:-mc}"
MC_CONFIG_DIR="$(mktemp -d)"
export MC_CONFIG_DIR
trap 'rm -rf "$MC_CONFIG_DIR"' EXIT
umask 077

mc_cmd() {
  if [[ -n "${MINIO_CA_FILE:-}" ]]; then
    SSL_CERT_FILE="$MINIO_CA_FILE" "$MC_BIN" "$@"
  else
    "$MC_BIN" "$@"
  fi
}

mc_cmd alias set source "$MINIO_SOURCE_URL" "$MINIO_SOURCE_ACCESS_KEY" "$MINIO_SOURCE_SECRET_KEY"
mc_cmd alias set target "$MINIO_TARGET_URL" "$MINIO_TARGET_ACCESS_KEY" "$MINIO_TARGET_SECRET_KEY"
mc_cmd ready source
mc_cmd ready target

require_versioning() {
  local path="$1"
  local info
  info="$(mc_cmd version info "$path" 2>/dev/null || true)"
  if ! grep -qi 'enabled' <<<"$info"; then
    echo "Versioning is not enabled on $path." >&2
    echo "Provision it with an administrator before configuring replication." >&2
    exit 2
  fi
}

ensure_source_bucket() {
  local bucket="$1"
  mc_cmd stat "source/$bucket" >/dev/null
  require_versioning "source/$bucket"
}

ensure_target_bucket() {
  local bucket="$1"
  local lock_required="$2"

  if ! mc_cmd stat "target/$bucket" >/dev/null 2>&1; then
    echo "Target bucket target/$bucket does not exist." >&2
    echo "Create it with the target administrator before configuring replication." >&2
    if [[ "$lock_required" == "yes" ]]; then
      echo "Required command: mc mb --with-lock target/$bucket" >&2
    else
      echo "Required command: mc mb --with-versioning target/$bucket" >&2
    fi
    exit 2
  fi

  require_versioning "target/$bucket"

  if [[ "$lock_required" == "yes" ]]; then
    # Query the bucket-level Object Lock configuration. Checking an object-level
    # retention entry would fail on a newly created, empty DR bucket.
    if ! mc_cmd retention info --default "target/$bucket" >/dev/null 2>&1; then
      echo "Target originals bucket has no Object Lock capability: target/$bucket" >&2
      echo "Recreate it with --with-lock before proceeding." >&2
      exit 3
    fi
  fi
}

has_replication_rule() {
  local bucket="$1"
  local output
  output="$(mc_cmd replicate ls "source/$bucket" --json 2>/dev/null || true)"
  [[ "$output" == *'Destination'* || "$output" == *'destination'* ]]
}

configure_bucket() {
  local bucket="$1"
  if has_replication_rule "$bucket"; then
    echo "Replication already configured for source/$bucket; refusing to add a duplicate rule."
    echo "Inspect with: mc replicate ls source/$bucket"
    return 0
  fi

  # Explicitly include existing object versions and delete semantics. With
  # Object Lock, protected versions still cannot be permanently deleted before
  # retention expires; retention/legal-hold metadata is replicated by MinIO.
  mc_cmd replicate add "source/$bucket" \
    --priority 1 \
    --remote-bucket "target/$bucket" \
    --replicate "delete,delete-marker,existing-objects"
}

ensure_source_bucket "$MINIO_BUCKET_ORIGINALS"
ensure_source_bucket "$MINIO_BUCKET_WORKING"
ensure_target_bucket "$MINIO_BUCKET_ORIGINALS" yes
ensure_target_bucket "$MINIO_BUCKET_WORKING" no

configure_bucket "$MINIO_BUCKET_ORIGINALS"
configure_bucket "$MINIO_BUCKET_WORKING"

echo
mc_cmd replicate ls "source/$MINIO_BUCKET_ORIGINALS"
mc_cmd replicate ls "source/$MINIO_BUCKET_WORKING"
echo "MinIO one-way DR replication is configured. Run check-replication.sh before declaring the target ready."
