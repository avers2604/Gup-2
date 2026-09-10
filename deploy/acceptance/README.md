# Этап 4 — стендовый acceptance cycle

Каталог содержит воспроизводимую процедуру приёмочных HA/DR испытаний. Скрипты
собирают evidence и делают итог fail-closed, но **не создают production
инфраструктуру и не выполняют разрушительные действия из CI**.

## Подготовка

На operations/bastion host нужны `patronictl`, `etcdctl`, `pgbackrest`, `curl`,
Python 3, MinIO Client (`mc`) и checkout приложения с рабочим virtualenv.

Цикл запускается от учётной записи ОС, которой соответствует роль PostgreSQL,
либо конфигурация pgBackRest задаёт `pgN-user` явно: `pgbackrest check`
подключается к БД под именем вызывающего пользователя ОС. Запуск из-под root на
типовой конфигурации даёт `FATAL: role "root" does not exist`
(docs/STAGE4_LAB_REHEARSAL.md, Ф-3).

Конфигурация pgBackRest на этом хосте обязана объявлять ВСЕ узлы кластера
(`pg1`/`pg2`/`pg3`). После switchover/failover primary оказывается на другом
узле, и объявление единственного `pg1` роняет проверку с
`ERROR: [027]: primary database not found` (Ф-5).

```bash
sudo install -d -o root -g root -m 0750 /etc/bz-get
sudo install -o root -g root -m 0600 \
  deploy/acceptance/acceptance.env.example \
  /etc/bz-get/stage4-acceptance.env
```

Заполнить реальные endpoints/TLS paths/credentials. Секреты в Git не
коммитятся. Для очередного испытания выбрать уникальный `RUN_ID`.

## 1. Fail-closed preflight

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 preflight
```

Preflight проверяет:

- `db1/db2/db3`, ровно один Patroni leader и здоровые replicas;
- replication lag;
- все три etcd endpoints через TLS;
- `pgbackrest check`, наличие и возраст backup;
- MinIO DR replication readiness;
- оба Alertmanager;
- `/health/` приложения.

Любая ошибка останавливает цикл **до** failure injection.

## 2. Холодная переиндексация 10 000 документов

Критерий ТЗ: **ровно 10 000 документов, не более 3600 секунд**.

Объём и лимит берутся из инвентаря (`SEARCH_REINDEX_DOCUMENTS`,
`SEARCH_REINDEX_MAX_SECONDS`) и записываются в `extended-results.env` вместе с
измерением. `finalize` сверяет прогон именно с записанным требованием, а не с
числом, зашитым в скрипт: занизить объём молча, не оставив следа в `RESULT.md`,
невозможно.

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/extended-checks.sh CHG-2026-0042 cold-reindex
```

`rebuild_search_index` делает cold rebuild persisted `DocumentSearchIndex`:
read-model очищается через `TRUNCATE`, затем FTS-векторы считаются в PostgreSQL
батчами (`SEARCH_REINDEX_BATCH_SIZE`, baseline 1000). Измерение сохраняется в
`search-reindex.json` и `extended-results.env`.

Если время > 60 минут, чекпоинт получает FAIL, а `RESULT.md` содержит явное
`DISCREPANCY`. До приёмки требуется либо оптимизация (batch size, DB resources,
параллельная стратегия/воркеры), либо документированное решение Заказчика об
изменении порога. Скрипт сам порог не повышает.

## 3. Planned/unplanned PostgreSQL и PITR

Разрушительные шаги выполняются оператором по `deploy/dr/REBUILD_AND_DRILL.md`.
Harness только фиксирует контрольные точки, например:

```bash
bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 checkpoint planned-switchover-start
bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 checkpoint planned-switchover-complete
bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 checkpoint unplanned-failover-start
bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 checkpoint unplanned-failover-complete
bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 checkpoint pitr-validated
```

CI не получает права делать `systemctl stop patroni`, promotion, DCS cleanup
или выбирать PITR target.

## 4. Аварийная остановка Celery/Redis под нагрузкой

Проверка использует специальные idempotent probe tasks. Celery/Redis работают
по модели **at-least-once**: после аварии одна и та же доставка может войти в
task повторно. Критерий приёмки — ни одной потерянной задачи и **ровно один
durable business completion на probe**. `delivery_count > 1` сохраняется как
evidence redelivery и не считается дублированием бизнес-эффекта.

Перед drill broker Redis должен использовать durable AOF baseline из
`deploy/redis/redis-broker.conf.example`; `CELERY_REDIS_VISIBILITY_TIMEOUT`
должен быть больше OCR hard limit 600 секунд (baseline 900 секунд).

### Worker SIGKILL

```bash
bash deploy/acceptance/extended-checks.sh CHG-2026-0042 queue-start worker
# убедиться, что часть probe находится STARTED
# на стенде: kill -9 <PID celery worker/child согласно change plan>
# восстановить worker и дождаться redelivery/завершения
bash deploy/acceptance/extended-checks.sh CHG-2026-0042 queue-verify worker
```

### Redis SIGKILL

```bash
bash deploy/acceptance/extended-checks.sh CHG-2026-0042 queue-start redis
# во время обработки: kill -9 <PID redis-server>
# запустить Redis с тем же durable AOF/data directory, затем worker
# дождаться завершения/redelivery
bash deploy/acceptance/extended-checks.sh CHG-2026-0042 queue-verify redis
```

`queue_drill verify` требует: ожидаемое число probe существует, каждая была
доставлена хотя бы раз и каждая имеет `completion_count == 1`. Любая потеря или
двойной durable effect делает acceptance FAIL.

## 5. MinIO failover/failback и SHA-256 выборка 500 файлов

Обычный `check-replication.sh` проверяет readiness/counts, но этого недостаточно
для приёмки. После переключения/восстановления выполняется криптографическая
проверка случайной выборки **ровно из 500 файлов**:

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/extended-checks.sh CHG-2026-0042 minio-hash
```

(`minio-hash-500` — прежнее имя того же действия, оно сохранено.)

`verify-500-hashes.sh` случайно выбирает объекты, читает каждый с source и
DR site, считает SHA-256 и пишет `minio-hash-sample.csv`. PASS требует, чтобы
число проверенных файлов совпало с требуемым и несовпадений было 0.

Размер выборки задаётся в инвентаре `MINIO_HASH_SAMPLE_SIZE`, по умолчанию 500.
Требуемое число пишется в `extended-results.env` и выводится в `RESULT.md`
отдельной строкой, поэтому снижение выборки — это видимое в отчёте решение
Заказчика, а не правка скрипта. Если в контрольном bucket меньше объектов, чем
требуется, проверка завершается FAIL.

## 5a. Измерение RTO с точки зрения приложения

Подключение psql к HAProxy даёт НЕ ТОТ показатель, который переживает АИС. На
репетиции (docs/STAGE4_LAB_REHEARSAL.md, Ф-4) прямое подключение к HAProxy
восстановилось за 0.4 с, а приложение, ходящее через PgBouncer, ещё 62 секунды
получало сессии с узлом в recovery, где падает любая запись. Разница в 150 раз.

Поэтому на каждом переключении RTO измеряется отдельным инструментом:

```bash
PGPASSWORD=... bash deploy/acceptance/write-path-probe.sh \
  --dsn-port 6432 --user bz_get --db bz_get --duration 180 \
  --out "$EVIDENCE/RUN_ID/write-path-switchover.csv"
```

Проба различает три состояния: `OK`, `READ_ONLY` (сессия есть, запись падает) и
`FAIL`. Для приёмки значимо окно `write unavailable` — сумма двух последних.
Любое ненулевое окно `READ_ONLY` означает, что переключение НЕ считается
успешным до решения Заказчика по Ф-4.

## 6. Alertmanager и application smoke

Проверить synthetic warning/critical/resolved, потерю одного Alertmanager peer и
работу Web/API после DB/MinIO переключений. Evidence и checkpoints сохраняются
в каталоге запуска.

## 7. Финализация

```bash
export ACCEPTANCE_DB_RESULT=PASS
export ACCEPTANCE_MINIO_RESULT=PASS
export ACCEPTANCE_ALERT_RESULT=PASS
export ACCEPTANCE_APP_RESULT=PASS
export ACCEPTANCE_INCIDENT_UTC=2026-09-10T19:00:00Z
export ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T18:59:50Z
export ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T19:03:00Z

bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 finalize
```

`RESULT.md` содержит:

- общий PASS/FAIL;
- observed RPO/RTO;
- cold reindex: число документов, требуемое число, время, лимит и результат;
- результат worker `kill -9` drill;
- результат Redis `kill -9` drill;
- размер MinIO SHA-256 sample, требуемый размер и число mismatches;
- пути/evidence для операторских HA/DR действий;
- по одной строке `DISCREPANCY` на каждое конкретное невыполненное условие
  (объём, время, статус измерения, mismatches, операторские флаги), чтобы отчёт
  называл настоящую причину, а не всегда лимит времени.

Даже если DB/MinIO/Alert/Application отмечены PASS, общий результат остаётся
FAIL, пока cold reindex, оба queue crash drill и 500-file hash verification не
выполнены успешно.

## CI

`validate.sh` проверяет shell/runtime-контракты harness без destructive
инфраструктуры: базовый preflight, RPO/RTO, новые extended gates, обязательный
FAIL при reindex > 3600 секунд и FAIL при `NOT_RUN`. Отдельно проверяются
отрицательные случаи: `/health/`, отдающий `unhealthy`, обязан валить preflight
(в CI используется та же якорная регулярка `^(ok|healthy|ready)$`, что и в
инвентаре, — неякорная приняла бы `unhealthy`); прогон, не добравший
собственный требуемый объём, обязан падать и обвинять объём, а не время; а
согласованный меньший объём обязан приниматься без правки скриптов. MinIO
validator отдельно проверяет механизм SHA-256 comparison через fake `mc`.

CI подтверждает корректность tooling, но не заменяет реальный стендовый запуск
и подпись Заказчика/эксплуатации.
