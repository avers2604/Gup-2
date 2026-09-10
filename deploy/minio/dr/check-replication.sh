#!/usr/bin/env bash
# Non-destructive MinIO DR validation. Uses dedicated replication credentials
# and compares replication state plus total visible object-version counts.
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

mc_cmd alias set source "$MINIO_SOURCE_URL" "$MINIO_SOURCE_ACCESS_KEY" "$MINIO_SOURCE_SECRET_KEY" >/dev/null
mc_cmd alias set target "$MINIO_TARGET_URL" "$MINIO_TARGET_ACCESS_KEY" "$MINIO_TARGET_SECRET_KEY" >/dev/null
mc_cmd ready source >/dev/null
mc_cmd ready target >/dev/null

failed=0

check_bucket() {
  local bucket="$1"
  echo "== $bucket =="

  if ! mc_cmd replicate ls "source/$bucket" >/dev/null; then
    echo "ERROR: no readable replication configuration for source/$bucket" >&2
    failed=1
    return
  fi

  if ! mc_cmd replicate status "source/$bucket"; then
    echo "ERROR: replication status failed for source/$bucket" >&2
    failed=1
  fi

  local source_versions target_versions
  source_versions="$(mc_cmd ls -r --versions "source/$bucket" 2>/dev/null | wc -l | tr -d ' ')"
  target_versions="$(mc_cmd ls -r --versions "target/$bucket" 2>/dev/null | wc -l | tr -d ' ')"
  echo "versions: source=$source_versions target=$target_versions"

  # Count mismatch is a hard readiness failure. Equal counts alone are not a
  # cryptographic proof of equality; the DR drill also checks selected objects
  # by stat/version/checksum before cutover.
  if [[ "$source_versions" != "$target_versions" ]]; then
    echo "ERROR: object-version count differs for $bucket" >&2
    failed=1
  fi
}

check_bucket "$MINIO_BUCKET_ORIGINALS"
check_bucket "$MINIO_BUCKET_WORKING"

if (( failed != 0 )); then
  echo "MinIO DR check FAILED" >&2
  exit 1
fi

echo "MinIO DR check passed. This is readiness evidence, not an RPO/RTO measurement."
