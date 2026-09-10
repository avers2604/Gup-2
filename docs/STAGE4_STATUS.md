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
- Grafana datasource + provisioned dashboard `BZ GET — Stage 4 HA / DR`;
- systemd units/env templates и CI/static validation.

Пороговые значения этих alert rules — bootstrap для стенда, а не SLA.

## Партия 3 — MinIO DR + Alertmanager delivery

Текущая ветка добавляет два ранее незакрытых контура.

### MinIO DR

`deploy/minio/dr/` реализует reference active-passive server-side bucket
replication только для application-бакетов:

```text
active MinIO site                 passive DR MinIO site
  bz-get-originals (WORM)   --->   bz-get-originals (WORM)
  bz-get-working            --->   bz-get-working
```

Выбран bucket replication, а не site replication: проекту не требуется
автоматически реплицировать весь IAM и все бакеты MinIO. Направление только
одно; reverse replication включается лишь после аварии, когда новый active site
зафиксирован, а старый site изолирован/rebuild'ится.

Добавлены:

- `dr.env.example` — endpoints/credentials вне Git;
- минимальные source/target replication policies;
- `configure-replication.sh` — Versioning + one-way replication существующих
  объектов и delete semantics;
- fail-closed проверка Object Lock capability на DR-копии `originals`;
- `check-replication.sh` — неразрушающая проверка правил/status/count версий;
- `validate.sh` — CI/static validation;
- `README.md` — failover, failback/resync и совместный PostgreSQL + MinIO
  restore drill.

`.env.example` теперь явно разделяет dev endpoint MinIO и production DR
endpoint: приложение должно использовать стабильное DNS/LB имя, а не знать,
какая площадка active.

Честная граница: bucket replication асинхронна, поэтому её наличие само по себе
не доказывает RPO=0. Реальный RPO определяется backlog/задержкой и измеряется на
DR drill.

### Alertmanager delivery

Добавлен `deploy/alertmanager/` и systemd unit двухузлового Alertmanager HA.
Prometheus отправляет каждый alert сразу обоим peers, без единственного LB между
Prometheus и Alertmanager.

Маршрутизация:

- `severity=critical` -> dedicated critical webhook;
- `severity=warning` -> warning webhook;
- `send_resolved=true`;
- critical ингибирует дублирующий warning того же события.

Receiver URL читается из root-owned `url_file`; токены/секретные webhook URLs
не хранятся в Git. Generic webhook оставляет выбор фактического канала
Заказчику: корпоративный relay, Service Desk, Mattermost/Telegram bridge и т.п.

Добавлены:

- Alertmanager routing config + HA env template;
- `get-alertmanager.service`;
- synthetic `test-alert.sh` для warning/critical с авто-expire;
- `validate.sh` с `amtool check-config`, если `amtool` установлен;
- Prometheus targets обоих Alertmanager;
- `deploy/prometheus/rules/alert-delivery.yml` — target down, degraded cluster,
  notification failures.

Честная граница: routing технически готов, но operational alerting считается
принятым только после заполнения реальных receiver URL и подтверждённого
synthetic delivery test на каждой площадке.

## Документационные ограничения, которые остаются обязательными

### Dev Compose не является HA/DR

Один PostgreSQL, один etcd и один MinIO container в `docker-compose.yml` —
только dev-среда. Несколько volume одного host не создают отдельный failure
domain.

### Реплика не является backup

PostgreSQL streaming replication и MinIO bucket replication защищают от разных
классов отказов, но не заменяют независимый backup/restore. Ошибка приложения
или оператора может распространиться на реплики.

### PostgreSQL и MinIO восстанавливаются согласованно

БД хранит metadata/object keys, файлы находятся в S3. Успешный `pgbackrest
restore` при отсутствующей соответствующей object version не считается
успешным DR АИС.

### Monitoring не равен SLA

Наличие alert rules/dashboard/Alertmanager не подтверждает время восстановления.
SLA возникает только после утверждённых RPO/RTO и измеренных drills.

## Что остаётся до закрытия Этапа 4

1. Развернуть 3x etcd + 3x Patroni/PostgreSQL на реальном стенде.
2. Развернуть две физически разделённые MinIO площадки и проверить bucket
   replication/Object Lock.
3. Развернуть два Alertmanager и подключить реальные warning/critical receivers.
4. Выполнить synthetic alert test с подтверждением доставки и отказом одного
   Alertmanager.
5. Выполнить planned switchover и unplanned PostgreSQL failover.
6. Выполнить full/diff/incr pgBackRest + continuous WAL и PITR.
7. Выполнить MinIO failover/failback с resync.
8. Выполнить **совместный PostgreSQL + MinIO restore drill** и проверить UUID,
   object keys, version IDs, checksums и WORM retention.
9. После успешного ручного restore автоматизировать rebuild Patroni из
   pgBackRest.
10. Утвердить RPO/RTO и заменить bootstrap thresholds на SLA-derived значения.

## Следующая партия

**Автоматизация rebuild Patroni из pgBackRest + объединённый failover/restore
drill.** Эта автоматизация должна появиться только после того, как ручной PITR
и MinIO DR-процедура воспроизводимы на стенде: разрушительный recovery path
нельзя автоматизировать раньше, чем доказана его корректность вручную.
