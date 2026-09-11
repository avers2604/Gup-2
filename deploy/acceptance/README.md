# Этап 4 — стендовый acceptance cycle

Каталог содержит воспроизводимую процедуру приёмочных HA/DR испытаний. Скрипты
собирают evidence и делают итог fail-closed, но **не создают production
инфраструктуру и не выполняют разрушительные действия из CI**.

## Подготовка

На operations/bastion host нужны `patronictl`, `etcdctl`, `pgbackrest`, `curl`,
Python 3, зависимости приложения (`boto3` для MinIO versioned verification) и
checkout приложения с рабочим virtualenv.

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

## Acceptance baseline и waiver

Авторитетные критерии приёмки находятся **не в локальном env**, а в
version-controlled `acceptance-policy.json`:

- cold rebuild: не менее **10 000** документов;
- cold rebuild: не более **3600 секунд**;
- MinIO DR: не менее **500 object versions**.

Локальный `acceptance.env` может сделать критерий строже без отдельного
согласования. Ослабить baseline простой правкой env нельзя: `preflight`
fail-closed остановится до стендовых действий.

Если Заказчик/эксплуатация формально согласовали исключение, одновременно
задаются:

```bash
ACCEPTANCE_WAIVER_ID=WAIVER-2026-0017
ACCEPTANCE_WAIVER_APPROVER='ФИО/роль утверждающего'
ACCEPTANCE_WAIVER_REASON='основание и границы исключения'
```

При полном waiver технически успешный прогон получает **`PASS_WITH_WAIVER`**,
а не обычный `PASS`. ID, утверждающий, ослабленные критерии и policy evidence
попадают в `RESULT.md`; такой результат нельзя представлять как безусловный
PASS. Неполный waiver (`ID` без approver/reason и т.п.) считается отсутствующим.

## 1. Fail-closed preflight

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/acceptance-cycle.sh CHG-2026-0042 preflight
```

Preflight сначала проверяет acceptance policy, затем инфраструктуру:

- критерии текущего запуска против repository baseline / waiver;
- `db1/db2/db3`, ровно один Patroni leader и здоровые replicas;
- replication lag;
- все три etcd endpoints через TLS;
- `pgbackrest check`, наличие и возраст backup;
- MinIO DR replication readiness;
- оба Alertmanager;
- `/health/` приложения.

Любая ошибка останавливает цикл **до** failure injection.

## 2. Настоящая холодная переиндексация

Acceptance-команда всегда запускает management command с `--cold`:

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/extended-checks.sh CHG-2026-0042 cold-reindex
```

В cold mode `DocumentSearchIndex` сначала очищается через `TRUNCATE`, после чего
FTS-векторы полностью восстанавливаются из source-of-truth документов батчами.
Измеряемое время включает очистку read-model. `--cold` требует точный
`--require-count`, поэтому destructive запуск не начинается, если corpus не
соответствует заявленному объёму.

Обычный `rebuild_search_index` без `--cold` остаётся production-safe online
UPSERT и **не является** acceptance evidence. `finalize` дополнительно требует
`search_reindex_mode=cold`, поэтому подмена cold-прогона обычным online refresh
не даст PASS.

Измерение сохраняется в `search-reindex.json` и `extended-results.env`. При
превышении эффективного лимита прогон FAIL; увеличить лимит выше repository
baseline можно только через waiver-механику выше.

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

## 5. MinIO failover/failback: versioned WORM выборка

Обычный `check-replication.sh` проверяет readiness/counts, но этого недостаточно
для приёмки. После переключения/восстановления выполняется выборка минимум
**500 конкретных object versions**:

```bash
ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  bash deploy/acceptance/extended-checks.sh CHG-2026-0042 minio-hash
```

(`minio-hash-500` — совместимое прежнее имя действия.)

`verify-500-hashes.sh` теперь является compatibility wrapper над
`verify_versioned_sample.py`. Для каждой выбранной source version проверяются:

- наличие на DR site **того же VersionId**;
- SHA-256 содержимого конкретной версии;
- Object Lock mode;
- `retain-until`;
- legal hold;
- факт, что source version действительно защищена retention или legal hold.

Evidence пишется в `minio-versioned-sample.csv`. Совпадение двух незащищённых
копий не считается PASS. Недостающая версия, несовпадение байтов или более
слабое/другое WORM-состояние дают FAIL.

Размер выборки берётся из `MINIO_HASH_SAMPLE_SIZE`, но repository baseline
требует минимум 500 versions. Уменьшить его можно только с полным waiver, и
финальный статус станет `PASS_WITH_WAIVER`.

## 5a. Измерение RTO с точки зрения приложения

Подключение psql к HAProxy даёт НЕ ТОТ показатель, который переживает АИС. На
репетиции (docs/STAGE4_LAB_REHEARSAL.md, Ф-4) прямое подключение к HAProxy
восстановилось за 0.4 с, а приложение, ходящее через PgBouncer, ещё 62 секунды
получало сессии с узлом в recovery, где падает любая запись.

После P0 guard это окно необходимо **перемерить**, а не считать автоматически
устранённым:

```bash
PGPASSWORD=... bash deploy/acceptance/write-path-probe.sh \
  --dsn-port 6432 --user bz_get --db bz_get --duration 180 \
  --out "$EVIDENCE/RUN_ID/write-path-switchover.csv"
```

Проба различает `OK`, `READ_ONLY` и `FAIL`. Для приёмки значимо полное окно
`write unavailable` (`READ_ONLY + FAIL`). Новое значение должно быть получено
на реальном стенде после установки PgBouncer primary guard; репозиторий не
подменяет это измерение расчётным значением.

## 6. Alertmanager, application smoke и business indicators

Проверить synthetic warning/critical/resolved, потерю одного Alertmanager peer и
работу Web/API после DB/MinIO переключений. Evidence и checkpoints сохраняются
в каталоге запуска.

Business dashboard также используется как sanity-check наблюдаемости:

- search counters считают только логический поиск (resolved page 1), а не
  переходы по страницам 2..N;
- zero-result ratio в Grafana рассчитывается через `increase()` за текущий
  `$__range`, а не из lifetime ratio;
- 403/404/504 link failures показываются как прирост за `$__range`;
- business 404/409 (неизвестный field/document, pending WORM promotion) не
  считаются инфраструктурным сбоем хранилища.

Эти показатели помогают интерпретировать стендовый прогон, но сами по себе не
заменяют HA/DR acceptance gates.

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

- `PASS`, `PASS_WITH_WAIVER` или `FAIL`;
- policy status, waiver ID/approver и список ослабленных критериев;
- observed RPO/RTO;
- search rebuild mode, число документов, время и лимит;
- результат worker `kill -9` drill;
- результат Redis `kill -9` drill;
- размер MinIO versioned WORM sample и число mismatches;
- пути/evidence операторских HA/DR действий;
- отдельный `DISCREPANCY` на каждое невыполненное условие.

Даже если DB/MinIO/Alert/Application отмечены PASS, общий результат остаётся
FAIL, пока cold reindex, оба queue crash drill и MinIO versioned WORM verification
не выполнены успешно. Waiver меняет только заранее согласованный acceptance
criterion; он не превращает фактический провал измерения в PASS.

## CI

`validate.sh` проверяет runtime-контракты harness без destructive
инфраструктуры:

- repository baseline и standalone policy tests;
- обязательный `--cold` path;
- обычный baseline `PASS`;
- запрет ослабления baseline без waiver;
- `PASS_WITH_WAIVER` только при полном ID/approver/reason;
- fail-closed при `NOT_RUN`, неправильном объёме и превышении времени;
- диагностический вывод при падении внешнего инструмента;
- корректность `/health/` проверки.

MinIO validator отдельно запускает unit tests version-aware verifier и проверяет,
что production wrapper больше не использует current-object-only hash comparison.
Monitoring validator проверяет period-scoped PromQL для search/link business
indicators, чтобы Grafana не подменяла выбранный период lifetime-значениями.

CI подтверждает корректность tooling, но не заменяет реальный стендовый запуск,
повторное измерение write-path RTO и подпись Заказчика/эксплуатации.
