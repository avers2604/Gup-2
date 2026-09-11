#!/usr/bin/env bash
# Compatibility wrapper for Stage 4 MinIO DR acceptance.
#
# Historical name retained because acceptance automation already calls it, but
# the check is no longer "current object bytes only". It now samples concrete
# non-null object versions and verifies that the DR site preserves the version
# ID, SHA-256, retention mode/date and legal-hold state.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE_SIZE="${MINIO_HASH_SAMPLE_SIZE:-500}"
SAMPLE_SEED="${MINIO_HASH_SAMPLE_SEED:-stage4}"
EVIDENCE_FILE="${MINIO_HASH_EVIDENCE_FILE:-minio-versioned-sample.csv}"

[[ "$SAMPLE_SIZE" =~ ^[0-9]+$ ]] && (( SAMPLE_SIZE > 0 )) || {
  echo "ERROR: MINIO_HASH_SAMPLE_SIZE must be a positive integer" >&2
  exit 64
}

exec python3 "$SCRIPT_DIR/verify_versioned_sample.py" \
  --sample-size "$SAMPLE_SIZE" \
  --seed "$SAMPLE_SEED" \
  --evidence "$EVIDENCE_FILE"
