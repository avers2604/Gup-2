# Этап 4, партия 4 — Patroni rebuild из pgBackRest и объединённый DR drill

Статус: **reference implementation / требуется реальный стенд**.

Эта партия автоматизирует восстановление **реплики Patroni** из pgBackRest и
сбор доказательств единого HA/DR испытания. Она намеренно не автоматизирует
создание нового primary после полной потери кластера: выбор recovery point,
изоляция старой площадки и первичное DR-восстановление остаются контролируемыми
операторскими действиями.

## 1. Архитектура replica rebuild

Patroni использует штатный список `create_replica_methods`:

```text
patronictl reinit
      |
      v
Patroni create_replica_methods
      |
      +--> pgbackrest -> patroni-pgbackrest-restore.sh
      |       |
      |       +--> approval marker?
      |       +--> valid backup?
      |       +--> PGDATA not running?
      |       +--> pgbackrest --delta restore
      |
      +--> basebackup (fallback while healthy leader exists)
```

В `deploy/ha/patroni.yml.example` порядок:

```yaml
create_replica_methods:
  - pgbackrest
  - basebackup
```

`pgbackrest` wrapper не задаёт `--target-action=promote`: этот путь создаёт
только standby/replica. После успешного restore дальнейшим запуском и
присоединением реплики управляет Patroni.

## 2. Установка wrapper на DB-узлах

На каждом PostgreSQL/Patroni узле:

```bash
sudo install -d -o root -g postgres -m 0755 /usr/local/libexec/bz-get
sudo install -o root -g postgres -m 0755 \
  deploy/dr/patroni-pgbackrest-restore.sh \
  /usr/local/libexec/bz-get/patroni-pgbackrest-restore.sh

sudo install -o root -g postgres -m 0755 \
  deploy/dr/rebuild-replica.sh \
  /usr/local/sbin/bz-get-rebuild-replica
```

Скопировать обновлённый `patroni.yml.example`, подставить параметры узла и
проверить конфигурацию. Изменение `create_replica_methods` должно применяться
по штатной процедуре Patroni; не выполнять несогласованный restart всех трёх
DB-узлов одновременно.

## 3. Approval gate

pgBackRest replica rebuild **запрещён по умолчанию**. Wrapper требует читаемый
marker:

```text
/etc/bz-get/dr/manual-pitr-approved
```

и строку:

```text
status=approved
```

Marker нельзя создавать `touch` вручную как обычный toggle. Для enablement
используется `approve-pgbackrest-rebuild.sh`, который принимает только drill,
где `RESULT.md` явно подписан:

```text
PASS/FAIL: PASS
```

Пример после реального ручного PITR/MinIO DR испытания:

```bash
sudo install -o root -g root -m 0755 \
  deploy/dr/approve-pgbackrest-rebuild.sh \
  /usr/local/sbin/bz-get-approve-pgbackrest-rebuild

sudo DRILL_EVIDENCE_ROOT=/var/lib/bz-get/drills \
  /usr/local/sbin/bz-get-approve-pgbackrest-rebuild \
  20260910-01 'change-CAB-operator' --execute
```

Marker содержит drill id, UTC approval timestamp и approver. Права по умолчанию
`root:postgres 0640`; Patroni/PostgreSQL может прочитать статус, но не изменить
его.

Чтобы немедленно отключить pgBackRest rebuild после инцидента или смены
политики:

```bash
sudo rm -f /etc/bz-get/dr/manual-pitr-approved
```

После этого custom method завершается fail-closed; при наличии здорового leader
Patroni может перейти к следующему методу `basebackup`.

## 4. Безопасный rebuild одной реплики

`rebuild-replica.sh` выполняет preflight:

1. читает `patronictl list --format=json`;
2. убеждается, что target существует и является replica/standby;
3. запрещает leader/primary;
4. требует ровно один writable leader;
5. проверяет pgBackRest stanza и наличие backup set;
6. требует approval marker;
7. по умолчанию работает как dry-run.

Dry-run:

```bash
sudo -u postgres \
  PATRONI_CONFIG=/etc/patroni/patroni.yml \
  /usr/local/sbin/bz-get-rebuild-replica db3
```

После change approval:

```bash
sudo -u postgres \
  PATRONI_CONFIG=/etc/patroni/patroni.yml \
  /usr/local/sbin/bz-get-rebuild-replica db3 --execute
```

Исполняемая команда внутри — штатный:

```text
patronictl reinit bz-get db3 --wait --force
```

После завершения script повторно проверяет роль и state реплики. Сам факт exit
code 0 не заменяет проверку replication lag, sync-standby и application probes.

## 5. Что этот rebuild НЕ делает

Он не должен:

- rebuild-ить leader/primary;
- удалять DCS state;
- выполнять `patronictl remove`;
- выбирать PITR target;
- выполнять `target-action=promote`;
- переключать HAProxy/DNS/MinIO endpoint;
- возвращать старую площадку в запись после split-brain риска.

Полная потеря PostgreSQL-кластера остаётся сценарием из `deploy/dr/README.md`:
сначала isolated recovery и бизнес-валидация, затем формирование нового
Patroni-кластера и rebuild его replicas.

## 6. Combined drill evidence harness

`combined-drill.sh` не инжектирует отказ самостоятельно. Он собирает
воспроизводимые timestamps/evidence вокруг действий оператора.

Подготовить root-readable env:

```bash
sudo install -d -o root -g postgres -m 0750 /etc/bz-get/dr
sudo install -o root -g postgres -m 0640 \
  deploy/dr/drill.env.example /etc/bz-get/dr/drill.env
sudoedit /etc/bz-get/dr/drill.env
```

Затем:

```bash
set -a
. /etc/bz-get/dr/drill.env
set +a

./deploy/dr/combined-drill.sh preflight
```

Preflight сохраняет:

- `patronictl list` JSON;
- `pgbackrest info` JSON;
- MinIO replication readiness;
- Alertmanager status, если endpoint задан;
- UTC checkpoints и metadata drill.

Рекомендуемые checkpoints единого испытания:

```bash
./deploy/dr/combined-drill.sh checkpoint incident-injected
./deploy/dr/combined-drill.sh checkpoint patroni-failover-complete
./deploy/dr/combined-drill.sh checkpoint application-db-path-restored
./deploy/dr/combined-drill.sh checkpoint minio-failover-complete
./deploy/dr/combined-drill.sh checkpoint pitr-restore-complete
./deploy/dr/combined-drill.sh checkpoint object-version-validation-complete
./deploy/dr/combined-drill.sh checkpoint alert-delivery-confirmed
./deploy/dr/combined-drill.sh checkpoint service-restored
```

После завершения заполнить в env фактические UTC значения:

```text
DRILL_INCIDENT_UTC=...
DRILL_LAST_DURABLE_UTC=...
DRILL_SERVICE_RESTORED_UTC=...
```

и выполнить:

```bash
./deploy/dr/combined-drill.sh finish
```

Результаты лежат в:

```text
$DRILL_EVIDENCE_ROOT/$DRILL_ID/
```

включая `RESULT.md` и `checkpoints.csv`.

## 7. Интерпретация RPO/RTO

Harness рассчитывает два наблюдаемых значения:

- `observed_rpo_upper_bound_seconds` = incident UTC − last durable UTC;
- `observed_rto_seconds` = service restored UTC − incident UTC.

Это **измерение конкретного drill**, а не автоматически утверждённый SLA.
Последняя durable точка должна подтверждаться одновременно PostgreSQL/WAL и
нужными MinIO object versions, иначе RPO нельзя считать доказанным для всей
АИС.

## 8. Обязательная программа объединённого испытания

### A. Planned switchover

- выполнить planned switchover Patroni;
- подтвердить HAProxy/PgBouncer routing;
- проверить Web/API и Celery;
- подтвердить Alertmanager firing/resolved цепочку.

### B. Unplanned DB leader loss

- инжектировать потерю leader на стенде;
- измерить время выбора нового leader;
- проверить отсутствие устойчивой очереди PgBouncer;
- rebuild-ить отказавший узел как replica;
- подтвердить sync standby и lag после стабилизации.

### C. Logical corruption / PITR

- создать контрольные данные;
- дождаться backup/WAL;
- выполнить тестовую логическую ошибку;
- остановить writers;
- восстановить PostgreSQL на isolated recovery host в точку до ошибки;
- валидировать бизнес-данные до promotion/cutover.

Для PITR по времени pgBackRest должен получать явный UTC target. Не выбирать
backup вручную без необходимости: pgBackRest умеет подобрать подходящий backup
для time target, но достижение recovery target всё равно проверяется по логам и
данным.

### D. MinIO DR

- проверить backlog replication;
- подтвердить нужные version IDs/checksums;
- подтвердить Object Lock/WORM `originals`;
- переключить stable S3 endpoint только после изоляции старого writer;
- после failback выполнить resync без active-active split-brain.

### E. Alert delivery

- synthetic warning + critical;
- отказ одного Alertmanager peer;
- подтверждение, что critical доходит получателю;
- проверка `resolved` notification.

### F. Full application validation

- UUID/metadata документов;
- object keys/version IDs;
- checksum файлов;
- WORM retention/legal hold;
- `/documents/`, Smart Search, External API;
- Celery/OCR smoke test;
- audit trail;
- новый full pgBackRest backup после стабилизации.

## 9. PASS criteria

Drill можно подписать `PASS/FAIL: PASS` только если:

1. новый Patroni leader единственный и writable;
2. replica rebuild воспроизводим штатной командой;
3. PostgreSQL recovery point соответствует ожидаемым бизнес-данным;
4. требуемые MinIO object versions существуют и checksums совпадают;
5. WORM `originals` не ослаблен;
6. application validation успешна;
7. critical alert доставлен при отказе одного Alertmanager;
8. фактические RPO/RTO записаны;
9. все ручные обходы/ошибки внесены в evidence;
10. второй оператор способен повторить runbook без устных инструкций.

При любом исключении результат остаётся FAIL/TBD и approval marker для
автоматического pgBackRest rebuild не создаётся.

## 10. CI coverage этой партии

`deploy/dr/validate-part4.sh` выполняет runtime-style проверку с fake
`patronictl`/`pgbackrest` и проверяет:

- shell syntax всех helpers;
- наличие `create_replica_methods`;
- dry-run не вызывает reinit;
- leader rebuild запрещён;
- unsigned/unapproved marker запрещён;
- pgBackRest restore получает `--delta`, но не promotion;
- combined harness создаёт evidence;
- расчёт тестового RPO=10s/RTO=180s;
- approval helper не принимает TBD и принимает подписанный PASS в dry-run.

Это защищает кодовую логику, но не заменяет испытание настоящих Patroni,
pgBackRest, MinIO и Alertmanager на целевой инфраструктуре.
