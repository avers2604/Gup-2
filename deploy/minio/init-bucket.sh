#!/usr/bin/env bash
# Создаёт два бакета MinIO с разными политиками хранения (ТЗ 4.3.1, 4.7;
# STACK.md → «Разделение политик хранения»):
#
#   originals — Object Locking (WORM) + Versioning. Хранит опубликованные,
#     неизменяемые артефакты: files_original карточек НРД и опубликованные
#     версии Template.file_editable / Template.file_sample. Retention задаётся
#     НА КАЖДУЮ ВЕРСИЮ ОБЪЕКТА при promote из приложения: у разных юридических
#     категорий разные Governance/Compliance/срок/Legal Hold, поэтому bucket-
#     default retention намеренно не используется.
#
#   working — mutable + Versioning. Хранит files_editable карточек НРД и
#     короткоживущий `staging/worm/`: туда новый immutable-файл попадает ДО
#     commit PostgreSQL. После commit приложение server-side-copy'ит его в
#     originals уже с Object Lock headers и удаляет staging-копию. Rollback
#     поэтому никогда не требует DELETE из WORM.
#
# Object Lock можно включить только при создании бакета на используемом
# baseline MinIO, поэтому существующий originals обязательно проверяется и
# скрипт завершается ошибкой, если bucket был когда-то создан без locking.
#
# Staging — страховочная зона, а не архив. Успешный promote удаляет объект
# сразу; lifecycle на `staging/worm/` удаляет забытые/rollback-объекты через
# 7 дней и их non-current версии ещё через 7 дней.
#
# Использование: ./deploy/minio/init-bucket.sh

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

docker compose run --rm mc sh -c '
  set -eu
  mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

  if mc ls "local/$MINIO_BUCKET_ORIGINALS" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_ORIGINALS уже существует; проверяю Object Lock."
  else
    mc mb --with-lock "local/$MINIO_BUCKET_ORIGINALS"
    echo "Бакет $MINIO_BUCKET_ORIGINALS создан с Object Locking (WORM) и Versioning."
  fi

  # `mc retention info --default` требует bucket с Object Lock enabled. Нам
  # важна именно capability, а отсутствие bucket-default retention ожидаемо:
  # приложение задаёт режим/дату/Legal Hold по юридической категории объекта.
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

  # Idempotent enough for repeated bootstrap runs: do not append another rule
  # when a lifecycle rule for this exact prefix already exists. If operations
  # intentionally changes the retention later, manage that rule explicitly by ID.
  lifecycle_json="$(mc ilm rule ls "local/$MINIO_BUCKET_WORKING" --json 2>/dev/null || true)"
  lifecycle_compact="$(printf "%s" "$lifecycle_json" | tr -d "[:space:]")"
  if printf "%s" "$lifecycle_compact" | grep -Fq '"Prefix":"staging/worm/"'; then
    echo "Lifecycle для staging/worm/ уже настроен; существующее правило оставлено без изменений."
  else
    mc ilm rule add \
      --prefix "staging/worm/" \
      --expire-days 7 \
      --noncurrent-expire-days 7 \
      "local/$MINIO_BUCKET_WORKING"
    echo "Lifecycle staging/worm/: current и non-current staging-объекты очищаются через 7 дней."
  fi
'
