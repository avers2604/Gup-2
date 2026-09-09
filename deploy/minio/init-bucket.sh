#!/usr/bin/env bash
# Создаёт бакет MinIO с включённым Object Locking (WORM) и Versioning —
# ТЗ 4.9 / STACK.md. Object Lock можно включить только при создании бакета,
# поэтому это отдельный шаг после `docker compose up -d`, а не часть образа.
#
# Использование: ./deploy/minio/init-bucket.sh
#
# ВАЖНО: конкретный режим/срок retention (governance vs compliance, число
# дней хранения скана после утраты силы документом) — открытый вопрос
# плана работ, требует решения Заказчика. Этот скрипт только включает саму
# возможность блокировки на бакете, но не задаёт retention по умолчанию —
# так `mc retention set` не проставляет политику молча, задача не решается
# самостоятельно.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

docker compose run --rm mc sh -c '
  set -e
  mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
  if mc ls "local/$MINIO_BUCKET_NAME" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_NAME уже существует — Object Lock задаётся только при создании, пропускаю."
  else
    mc mb --with-lock "local/$MINIO_BUCKET_NAME"
    echo "Бакет $MINIO_BUCKET_NAME создан с Object Locking (WORM) и Versioning."
  fi
'
