#!/usr/bin/env bash
# Bootstrap MinIO with a root-only control plane and a least-privilege
# application identity. The application identity may mutate `working` but can
# only append/read/verify Object-Locked objects in `originals`; it has no
# DeleteObject/DeleteObjectVersion/BypassGovernanceRetention capability there.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

compose=(docker compose)
infra_env="${INFRA_ENV_FILE:-deploy/infra.env}"
if [[ -f "$infra_env" ]]; then
  compose+=(--env-file "$infra_env")
fi

"${compose[@]}" run --rm mc sh -c '
  set -eu
  mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

  case "$MINIO_BUCKET_ORIGINALS" in
    ""|*[!a-z0-9.-]*) echo "ERROR: invalid originals bucket name" >&2; exit 2 ;;
  esac
  case "$MINIO_BUCKET_WORKING" in
    ""|*[!a-z0-9.-]*) echo "ERROR: invalid working bucket name" >&2; exit 2 ;;
  esac
  if [ "$MINIO_BUCKET_ORIGINALS" = "$MINIO_BUCKET_WORKING" ]; then
    echo "ERROR: originals and working buckets must differ" >&2
    exit 2
  fi

  if mc ls "local/$MINIO_BUCKET_ORIGINALS" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_ORIGINALS уже существует; проверяю Object Lock."
  else
    mc mb --with-lock "local/$MINIO_BUCKET_ORIGINALS"
    echo "Бакет $MINIO_BUCKET_ORIGINALS создан с Object Locking (WORM) и Versioning."
  fi

  if ! mc retention info --default "local/$MINIO_BUCKET_ORIGINALS" >/tmp/originals-retention.txt 2>&1; then
    cat /tmp/originals-retention.txt >&2 || true
    echo "ERROR: $MINIO_BUCKET_ORIGINALS не подтверждает Object Lock. Пересоздавать production bucket автоматически запрещено." >&2
    exit 2
  fi

  if mc ls "local/$MINIO_BUCKET_WORKING" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_WORKING уже существует."
  else
    mc mb "local/$MINIO_BUCKET_WORKING"
    echo "Бакет $MINIO_BUCKET_WORKING создан без Object Lock."
  fi
  mc version enable "local/$MINIO_BUCKET_WORKING"

  lifecycle_json="$(mc ilm rule ls "local/$MINIO_BUCKET_WORKING" --json 2>/dev/null || true)"
  lifecycle_compact="$(printf "%s" "$lifecycle_json" | tr -d "[:space:]")"
  if printf "%s" "$lifecycle_compact" | grep -Fq '"'"'"Prefix"'"'":"'"'"staging/worm/"'"'"'; then
    echo "Lifecycle для staging/worm/ уже настроен; существующее правило оставлено без изменений."
  else
    mc ilm rule add \
      --prefix "staging/worm/" \
      --expire-days 7 \
      --noncurrent-expire-days 7 \
      "local/$MINIO_BUCKET_WORKING"
    echo "Lifecycle staging/worm/: current и non-current staging-объекты очищаются через 7 дней."
  fi

  sed \
    -e "s/__ORIGINALS_BUCKET__/$MINIO_BUCKET_ORIGINALS/g" \
    -e "s/__WORKING_BUCKET__/$MINIO_BUCKET_WORKING/g" \
    /config/app-policy.json.tpl > /tmp/bz-get-app-policy.json

  mc admin user add local "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
  mc admin policy create local bz-get-app /tmp/bz-get-app-policy.json
  mc admin policy attach local bz-get-app --user "$MINIO_ACCESS_KEY"
  echo "MinIO application identity configured with least-privilege WORM policy."
'
