# Этап 4 — фактический статус репозитория

Дата перепроверки: 2026-09-10.

Этот файл фиксирует состояние кода и эксплуатационных шаблонов. Наличие
конфигураций в Git не считается доказательством пройденного HA/DR испытания и
не подтверждает RPO/RTO без стендовых измерений.

## Что было до начала Этапа 4

В `main` уже были одиночные PostgreSQL/PgBouncer/etcd для разработки, MinIO,
Redis, ClamAV, Prometheus и Grafana. `STACK.md` фиксировал Patroni и pgBackRest
как целевые компоненты, но production/reference-конфигураций HA и DR в
репозитории не было. Prometheus выполнял только self-scrape.

## Партия 1 — HA PostgreSQL + DR bootstrap

После merge PR #29 в `main` присутствуют:

### `deploy/ha/`

- `patroni.yml.example` — baseline для 3 PostgreSQL/Patroni узлов;
- `etcd.env.example` — трёхузловой etcd/TLS baseline;
- `haproxy.cfg.example` — выбор current primary через Patroni REST;
- `pgbouncer.ini.example` — локальный transaction pool приложения;
- `validate.sh` — неразрушающая проверка конфигурации;
- `README.md` — bootstrap, switchover/failover и Definition of Done.

### `deploy/dr/`

- `pgbackrest.conf.example` — encrypted remote repository + WAL archive;
- `check-backup.sh` — штатная проверка stanza/repository;
- `README.md` — PITR, полная потеря DB-кластера, restore drill и форма
  фиксации фактических RPO/RTO.

Django в HA-среде должен подключаться к стабильной локальной точке
`127.0.0.1:6432`:

```text
Django -> PgBouncer -> HAProxy -> current Patroni primary
```

## Партия 2 — monitoring HA/DR

Текущая ветка добавляет monitoring/reference implementation поверх партии 1.

### Patroni

Используется нативный endpoint Patroni 4.1.x `GET /metrics`. Prometheus
опрашивает все три DB-узла и контролирует:

- ровно один primary;
- наличие sync standby;
- состояние PostgreSQL;
- replay lag;
- свежесть связи Patroni с DCS;
- failsafe mode;
- pending restart.

### PostgreSQL

На каждом DB-узле предусматривается `postgres_exporter` под отдельной
read-only учётной записью с predefined role `pg_monitor`. Application user и
PostgreSQL superuser для мониторинга не используются.

### PgBouncer

На каждом app-узле предусматривается community `pgbouncer_exporter`. Для него
в PgBouncer выделен отдельный `stats_users = pgbouncer_exporter`, без выдачи
`admin_users`. Контролируются waiting clients и max client wait.

### pgBackRest

Добавлен небольшой read-only exporter `deploy/monitoring/pgbackrest_exporter.py`.
Он использует стабильный JSON интерфейс `pgbackrest info --output=json`, не
запускает backup/restore и отдаёт:

- exporter/stanza health;
- наличие WAL archive range;
- число backup по типам;
- timestamp/age последних full/diff/incr/any backup.

### Prometheus / Grafana

Добавлены:

- `deploy/prometheus/prometheus.stage4.yml.example`;
- `deploy/prometheus/rules/ha-dr.yml`;
- Grafana datasource `Infrastructure Prometheus`;
- provisioned dashboard `BZ GET — Stage 4 HA / DR`;
- systemd units и env-шаблоны exporter'ов;
- `deploy/monitoring/validate.sh`.

Пороговые значения в alert rules — **bootstrap для стенда**, а не утверждённый
SLA. Например, backup >8 часов считается stale исходя из временного стендового
schedule incremental-раз-в-6-часов; после утверждения RPO/RTO пороги должны
быть пересчитаны.

## Что документация больше не должна утверждать

### Dev Compose не является HA

Один PostgreSQL и один etcd в `docker-compose.yml` остаются только dev-средой.
Их нельзя выдавать за production HA-кластер независимо от количества volume.

### Реплика не является backup

Streaming replication защищает от отказа узла, но не от логической ошибки.
DR PostgreSQL опирается на отдельный pgBackRest repository и PITR.

### MinIO в одном host не является DR

Четыре data directory одного MinIO-контейнера не защищают от потери узла.
Полное восстановление АИС требует согласованной пары PostgreSQL metadata + S3
objects; MinIO DR остаётся отдельной незакрытой подпартией Этапа 4.

### Monitoring не равен operational alerting

Prometheus уже может вычислять правила, Grafana — отображать состояние. Но
корпоративный канал доставки critical/warning уведомлений ещё не выбран.
Пока не настроен и не испытан Alertmanager/Grafana contact point, нельзя
считать оповещение дежурной смены завершённым.

## Что остаётся до закрытия Этапа 4

1. Развернуть 3x etcd + 3x Patroni/PostgreSQL на реальном стенде.
2. Выполнить planned switchover и unplanned failover с измерением времени.
3. Проверить exporter'ы и алерты во время фактического отказа узла.
4. Настроить и испытать канал доставки alert notifications.
5. Выполнить full/diff/incr backup и непрерывный WAL archive.
6. Выполнить PITR на отдельный recovery host.
7. Выполнить восстановление после полной потери тестового DB-кластера.
8. Реализовать MinIO replication/backup/restore с учётом WORM `originals`.
9. После проверенного ручного restore автоматизировать rebuild Patroni из
   pgBackRest.
10. Утвердить RPO/RTO и заменить bootstrap thresholds на SLA-derived значения.

## Ближайшая следующая партия

**MinIO DR + доставка alert notifications.** После этого — автоматизация
Patroni rebuild из pgBackRest и объединённый failover/restore drill с
измерением фактических RPO/RTO.
