# Этап 4 — фактический статус репозитория

Дата перепроверки: 2026-09-10.

Этот файл фиксирует состояние кода и эксплуатационных шаблонов. Наличие
конфигураций в Git не считается доказательством пройденного HA/DR испытания и
не подтверждает RPO/RTO без стендовых измерений.

## Партия 1 — HA PostgreSQL + DR bootstrap

После merge PR #29 в `main` присутствуют:

- `deploy/ha/` — Patroni/PostgreSQL 3-node baseline, 3-node etcd/TLS,
  локальные HAProxy + PgBouncer, failover/switchover runbook;
- `deploy/dr/` — pgBackRest, continuous WAL archive, PITR и сценарий полной
  потери PostgreSQL-кластера;
- стабильная DB-точка приложения `127.0.0.1:6432` через
  `Django -> PgBouncer -> HAProxy -> Patroni primary`.

Честная граница: это reference implementation. Реальный кластер, failover drill
и измеренные RPO/RTO требуют стенда.

## Партия 2 — monitoring HA/DR

После merge PR #30 в `main` присутствуют:

- native Patroni `/metrics`;
- `postgres_exporter` под отдельной read-only ролью `pg_monitor`;
- `pgbouncer_exporter` под отдельным `stats_users`;
- read-only `pgbackrest_exporter.py`;
- `deploy/prometheus/prometheus.stage4.yml.example`;
- правила `deploy/prometheus/rules/ha-dr.yml`;
- Grafana datasource + dashboard `BZ GET — Stage 4 HA / DR`;
- systemd units/env templates и CI/static validation.

Пороговые значения alert rules — bootstrap для стенда, а не SLA.

## Партия 3 — MinIO DR + Alertmanager delivery

После merge PR #31 в `main` присутствуют:

### MinIO DR

`deploy/minio/dr/` реализует reference active-passive bucket replication:

```text
active MinIO site                 passive DR MinIO site
  bz-get-originals (WORM)   --->   bz-get-originals (WORM)
  bz-get-working            --->   bz-get-working
```

Репликация только однонаправленная. `originals` требует Object Lock/WORM и
Versioning на обеих площадках. Есть least-privilege replication policies,
non-destructive readiness check и failover/failback/resync runbook.

### Alertmanager delivery

Добавлен двухузловой Alertmanager HA. Prometheus отправляет alerts обоим peers;
`warning`/`critical` маршрутизируются отдельно, receiver URL лежат вне Git.
Есть synthetic alert test и self-monitoring Alertmanager.

Честная граница: operational alerting считается принятым только после реальной
настройки receiver URL и подтверждения доставки на стенде.

## Партия 4 — guarded Patroni rebuild из pgBackRest + combined drill

Текущая ветка `stage4/patroni-rebuild-drill` добавляет следующую подпартию.

### Patroni replica rebuild

`deploy/ha/patroni.yml.example` теперь использует штатный порядок:

```text
create_replica_methods = pgbackrest -> basebackup
```

Custom method `deploy/dr/patroni-pgbackrest-restore.sh`:

- принимает параметры replica creation от Patroni;
- проверяет scope/data directory;
- запрещает использование не для replica;
- проверяет, что PostgreSQL в target PGDATA не запущен;
- проверяет pgBackRest metadata и наличие backup set;
- выполняет `pgbackrest --delta restore`;
- никогда не задаёт `target-action=promote`;
- fail-closed до появления подписанного approval marker.

`deploy/dr/rebuild-replica.sh` оборачивает штатный `patronictl reinit --wait
--force`, работает dry-run по умолчанию, запрещает rebuild leader/primary и
требует ровно один writable leader перед выполнением.

### Approval gate

Автоматический pgBackRest path не включается простым наличием кода.
`approve-pgbackrest-rebuild.sh` принимает только drill с явно подписанным:

```text
PASS/FAIL: PASS
```

и создаёт root-owned marker `/etc/bz-get/dr/manual-pitr-approved` со статусом,
drill id, временем и approver. До этого Patroni custom restore завершается
ошибкой и не считается разрешённым recovery path.

### Combined drill evidence

`combined-drill.sh` не инжектирует отказ и не выполняет destructive recovery
самостоятельно. Он собирает единый evidence set вокруг operator-run действий:

- Patroni topology/state;
- pgBackRest backup metadata;
- MinIO replication readiness;
- Alertmanager status;
- UTC checkpoints;
- observed RPO upper bound;
- observed RTO;
- финальный `RESULT.md`.

Подробная программа испытаний находится в `deploy/dr/REBUILD_AND_DRILL.md`.

### CI

`deploy/dr/validate-part4.sh` использует fake `patronictl`/`pgbackrest` и
проверяет runtime-контракты без production infrastructure: dry-run, запрет
leader rebuild, approval marker, restore args, evidence generation и расчёт
RPO/RTO. Проверка подключена в `.github/workflows/ci.yml`.

## Обязательные архитектурные ограничения

### Dev Compose не является HA/DR

Один PostgreSQL, один etcd и один MinIO container в `docker-compose.yml` —
только dev-среда. Несколько volume одного host не создают отдельный failure
domain.

### Реплика не является backup

PostgreSQL streaming replication и MinIO bucket replication защищают от разных
классов отказов, но не заменяют независимый backup/restore.

### PostgreSQL и MinIO восстанавливаются согласованно

БД хранит metadata/object keys, файлы находятся в S3. Успешный PostgreSQL
restore при отсутствующей соответствующей object version не считается
успешным DR всей АИС.

### Replica rebuild не является full-cluster recovery

Автоматизированный `patronictl reinit` применяется только к replica/standby.
Он не удаляет DCS, не выбирает PITR target и не создаёт новый primary после
полной потери кластера. Full-cluster recovery остаётся отдельной контролируемой
процедурой с isolated validation.

### Monitoring не равен SLA

Наличие alert rules/dashboard/Alertmanager и автоматизированного evidence
collector не подтверждает время восстановления. SLA возникает после
утверждённых RPO/RTO и измеренных drills.

## Что остаётся до закрытия Этапа 4

1. Развернуть 3x etcd + 3x Patroni/PostgreSQL на реальном стенде.
2. Развернуть две физически разделённые MinIO площадки и проверить replication/Object Lock.
3. Развернуть два Alertmanager и подключить реальные warning/critical receivers.
4. Выполнить synthetic alert test с подтверждением доставки и отказом одного Alertmanager.
5. Выполнить planned switchover и unplanned PostgreSQL failover.
6. Выполнить full/diff/incr pgBackRest + continuous WAL и ручной PITR на isolated recovery host.
7. Выполнить MinIO failover/failback с resync.
8. Выполнить совместный PostgreSQL + MinIO restore drill и подписать evidence PASS/FAIL.
9. Только после PASS включить approval marker и проверить pgBackRest-based rebuild одной Patroni replica.
10. Повторить полный combined drill вторым оператором и зафиксировать фактические RPO/RTO.
11. Утвердить RPO/RTO и заменить bootstrap thresholds на SLA-derived значения.

## Следующая граница после партии 4

После успешного реального combined drill код Этапа 4 можно считать функционально
собранным, но не эксплуатационно принятым. Следующая работа — устранение
результатов стендовых испытаний, формализация утверждённых RPO/RTO и, только при
необходимости, отдельная автоматизация **full-cluster** recovery. Автоматически
удалять DCS/создавать новый primary в этой партии сознательно запрещено.
