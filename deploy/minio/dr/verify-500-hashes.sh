#!/usr/bin/env bash
# Cryptographic sample verification for MinIO DR acceptance.
set -euo pipefail
umask 077

: "${MINIO_SOURCE_URL:?set MINIO_SOURCE_URL}"
: "${MINIO_TARGET_URL:?set MINIO_TARGET_URL}"
: "${MINIO_SOURCE_ACCESS_KEY:?set MINIO_SOURCE_ACCESS_KEY}"
: "${MINIO_SOURCE_SECRET_KEY:?set MINIO_SOURCE_SECRET_KEY}"
: "${MINIO_TARGET_ACCESS_KEY:?set MINIO_TARGET_ACCESS_KEY}"
: "${MINIO_TARGET_SECRET_KEY:?set MINIO_TARGET_SECRET_KEY}"
: "${MINIO_BUCKET_ORIGINALS:?set MINIO_BUCKET_ORIGINALS}"

MC_BIN="${MC_BIN:-mc}"
SAMPLE_SIZE="${MINIO_HASH_SAMPLE_SIZE:-500}"
SAMPLE_SEED="${MINIO_HASH_SAMPLE_SEED:-stage4}"
SAMPLE_BUCKET="${MINIO_HASH_SAMPLE_BUCKET:-$MINIO_BUCKET_ORIGINALS}"
EVIDENCE_FILE="${MINIO_HASH_EVIDENCE_FILE:-minio-hash-sample.csv}"

[[ "$SAMPLE_SIZE" =~ ^[0-9]+$ ]] && (( SAMPLE_SIZE > 0 )) || {
  echo "ERROR: MINIO_HASH_SAMPLE_SIZE must be a positive integer" >&2
  exit 64
}

MC_CONFIG_DIR="$(mktemp -d)"
objects_json="$(mktemp)"
sample_b64="$(mktemp)"
trap 'rm -rf "$MC_CONFIG_DIR" "$objects_json" "$sample_b64"' EXIT
export MC_CONFIG_DIR

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
mc_cmd ls -r --json "source/$SAMPLE_BUCKET" >"$objects_json"

python3 - "$objects_json" "$SAMPLE_SIZE" "$SAMPLE_SEED" >"$sample_b64" <<'PY'
import base64
import json
import random
import sys
from pathlib import Path

path, size_raw, seed = sys.argv[1:]
keys = []
for line in Path(path).read_text().splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    if row.get("type") in ("folder", "directory"):
        continue
    key = row.get("key")
    if key:
        keys.append(str(key))
size = int(size_raw)
if len(keys) < size:
    raise SystemExit(f"need at least {size} files for acceptance sample; found {len(keys)}")
rng = random.Random(seed)
for key in rng.sample(keys, size):
    print(base64.b64encode(key.encode()).decode())
PY

printf 'key_base64,source_sha256,target_sha256,result\n' >"$EVIDENCE_FILE"
checked=0
failed=0
while IFS= read -r encoded; do
  [[ -n "$encoded" ]] || continue
  key="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$encoded")"
  source_hash="$(mc_cmd cat "source/$SAMPLE_BUCKET/$key" | sha256sum | awk '{print $1}')"
  target_hash="$(mc_cmd cat "target/$SAMPLE_BUCKET/$key" | sha256sum | awk '{print $1}')"
  result=PASS
  if [[ "$source_hash" != "$target_hash" ]]; then
    result=FAIL
    failed=$((failed + 1))
  fi
  printf '%s,%s,%s,%s\n' "$encoded" "$source_hash" "$target_hash" "$result" >>"$EVIDENCE_FILE"
  checked=$((checked + 1))
done <"$sample_b64"

if (( checked != SAMPLE_SIZE )); then
  echo "ERROR: expected $SAMPLE_SIZE files, checked $checked" >&2
  exit 1
fi
if (( failed != 0 )); then
  echo "ERROR: SHA-256 mismatch for $failed/$checked files; evidence=$EVIDENCE_FILE" >&2
  exit 1
fi

echo "MinIO hash sample PASS: checked=$checked mismatches=0 bucket=$SAMPLE_BUCKET evidence=$EVIDENCE_FILE"
