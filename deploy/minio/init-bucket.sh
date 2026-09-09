#!/usr/bin/env bash
# Создаёт два бакета MinIO с разными политиками хранения (ТЗ 4.3.1, 4.7;
# STACK.md → «Разделение политик хранения»):
#
#   originals — Object Locking (WORM) + Versioning. Хранит только
#     files_original карточки НРД (защищённые от изменения сканы приказов).
#     Object Lock можно включить ТОЛЬКО при создании бакета, поэтому это
#     отдельный шаг после `docker compose up -d`, а не часть образа.
#
#   working — без блокировки. Хранит files_editable НРД и файлы бланков
#     (Template.file_editable, Template.file_sample) — по ТЗ 4.3.1 минорная
#     корректировка правомерно заменяет файл бланка без прерывания
#     жизненного цикла записи, что несовместимо со строгим Object Lock.
#     Versioning включён отдельно — он не блокирует перезапись, только
#     сохраняет историю версий объекта.
#
# Использование: ./deploy/minio/init-bucket.sh
#
# ВАЖНО: конкретный режим/срок retention на бакете originals (Governance
# vs Compliance, число дней хранения скана после утраты силы документом) —
# открытый вопрос плана работ, требует решения Заказчика. Этот скрипт
# только включает саму возможность блокировки, но не задаёт retention по
# умолчанию — задача не решается самостоятельно молчаливой командой.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

docker compose run --rm mc sh -c '
  set -e
  mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

  if mc ls "local/$MINIO_BUCKET_ORIGINALS" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_ORIGINALS уже существует — Object Lock задаётся только при создании, пропускаю."
  else
    mc mb --with-lock "local/$MINIO_BUCKET_ORIGINALS"
    echo "Бакет $MINIO_BUCKET_ORIGINALS создан с Object Locking (WORM) и Versioning."
  fi

  if mc ls "local/$MINIO_BUCKET_WORKING" >/dev/null 2>&1; then
    echo "Бакет $MINIO_BUCKET_WORKING уже существует, пропускаю."
  else
    mc mb "local/$MINIO_BUCKET_WORKING"
    mc version enable "local/$MINIO_BUCKET_WORKING"
    echo "Бакет $MINIO_BUCKET_WORKING создан без блокировки, Versioning включён (историю можно чистить, перезапись разрешена)."
  fi
'
