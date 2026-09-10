# Этап 4 — monitoring HA/DR

Статус: **reference implementation / требуется стендовая проверка**.

Эта партия добавляет мониторинг PostgreSQL/Patroni, PgBouncer и pgBackRest.
Правила оценивает Prometheus, Grafana используется для визуализации. Канал
доставки уведомлений (Alertmanager/Grafana contact point) должен быть выбран
Заказчиком; до этого алерты видны в Prometheus/Grafana, но не считаются
оперативным оповещением дежурной смены.

## Архитектура

```text
DB nodes
  Patroni :8008 /metrics ---------+
  postgres_exporter :9187 --------+----> Prometheus ----> Grafana
                                   |          |
App nodes                          |          +--> HA/DR alert rules
  PgBouncer exporter :9127 -------+
                                   |
Backup/ops node                    |
  pgBackRest exporter :9854 ------+
```

Все exporter-порты должны быть доступны только из monitoring/operations
сети. Не публиковать их в пользовательскую сеть или интернет.

## 1. Patroni

Patroni 4.1.x уже отдаёт Prometheus-метрики через `GET /metrics`; отдельный
exporter не требуется. `prometheus.stage4.yml.example` опрашивает все три
DB-узла.

Основные сигналы:

- `patroni_primary` — количество primary должно быть ровно 1;
- `patroni_sync_standby` — baseline Этапа 4 ожидает минимум одну sync standby;
- `patroni_postgres_running` — состояние PostgreSQL на каждом узле;
- `patroni_dcs_last_seen` — свежесть контакта с etcd;
- `patroni_failsafe_mode_is_active` — аварийный режим DCS;
- `patroni_pending_restart` — конфигурация требует rolling restart;
- `patroni_xlog_location`/`patroni_xlog_replayed_location` — replay lag.

`deploy/prometheus/rules/ha-dr.yml` содержит alert rules поверх этих метрик.

## 2. postgres_exporter

На каждом DB-узле exporter запускается локально под отдельной OS-учёткой
`postgres_exporter`. Подключение к PostgreSQL идёт через Unix socket и уже
существующее правило Patroni `local all all peer`, поэтому отдельный пароль БД,
client certificate и сетевой доступ к 5432 exporter'у не нужны.

Порядок:

1. создать OS-учётку `postgres_exporter` без shell;
2. выполнить `postgres-exporter-role.sql` от PostgreSQL superuser;
3. положить `postgres-exporter.env.example` как
   `/etc/bz-get/postgres-exporter.env`;
4. установить бинарник `postgres_exporter` в `/usr/local/bin/`;
5. установить `deploy/systemd/get-postgres-exporter.service`;
6. разрешить TCP/9187 только от Prometheus.

SQL-роль получает `INHERIT`, predefined role `pg_monitor` и `CONNECT` к
`bz_get`. `INHERIT` важен: без него права `pg_monitor` не применялись бы к
обычным запросам exporter'а. Application user и PostgreSQL superuser credentials
для мониторинга не используются.

Проверка:

```bash
sudo -u postgres_exporter psql -h /var/run/postgresql -d bz_get -c 'select 1'
curl http://DB_MONITORING_IP:9187/metrics | grep '^pg_up'
```

Ожидается `pg_up 1`.

## 3. PgBouncer exporter

На каждом app-узле создаётся отдельный пользователь `pgbouncer_exporter`.
В `deploy/ha/pgbouncer.ini.example` он включён только в `stats_users`, не в
`admin_users`. Также добавлен `ignore_startup_parameters = extra_float_digits`,
который требуется community `pgbouncer_exporter`.

`userlist.txt` должен содержать SCRAM verifier для `pgbouncer_exporter`.
Реальный пароль в Git не хранится. Env-переменная называется
`PGBOUNCER_EXPORTER_CONNECTION_STRING` — это имя, которое поддерживает
актуальный exporter.

Установка:

```text
/etc/bz-get/pgbouncer-exporter.env  <- шаблон из deploy/monitoring/
/usr/local/bin/pgbouncer_exporter
/etc/systemd/system/get-pgbouncer-exporter.service
```

Проверка:

```bash
curl http://APP_MONITORING_IP:9127/metrics | grep '^pgbouncer_'
```

Ключевые сигналы — waiting clients и max wait. Постоянная очередь означает,
что проблема уже видна пользователю даже если сам PostgreSQL формально healthy.

## 4. pgBackRest exporter

`pgbackrest_exporter.py` — небольшой read-only exporter на Python stdlib. Он:

- запускает только `pgbackrest info --output=json`;
- не выполняет `backup`, `expire`, `restore` или `stanza-create`;
- кэширует результат по умолчанию на 60 секунд;
- экспортирует состояние stanza, наличие WAL archive range, число backup и
  возраст последних full/diff/incr/any backup;
- при любой ошибке продолжает отдавать валидный `/metrics` с
  `pgbackrest_exporter_up 0`, чтобы сбой был виден Prometheus.

Рекомендуемое место запуска — backup/operations host, имеющий read access к
pgBackRest repository. Его `pgbackrest.conf` должен описывать repository с
точки зрения этого host; конфигурацию DB-узла с `repo1-host` нельзя механически
копировать на сам repository-host, если это создаёт петлю подключения.

Установить скрипт как `/usr/local/libexec/bz-get/pgbackrest_exporter.py`,
env-файл — `/etc/bz-get/pgbackrest-exporter.env`, unit —
`get-pgbackrest-exporter.service`.

Проверка:

```bash
curl http://BACKUP_MONITORING_IP:9854/-/healthy
curl http://BACKUP_MONITORING_IP:9854/metrics
```

`/-/healthy` проверяет только процесс exporter'а. Истинное состояние repository
показывают `pgbackrest_exporter_up` и `pgbackrest_stanza_ok`.

## 5. Prometheus

`deploy/prometheus/prometheus.yml` остаётся минимальной dev-конфигурацией.
Для HA-стенда используется `prometheus.stage4.yml.example`; адреса `db1/db2/db3`,
`app1/app2`, `backup1` заменяются на адресный план среды.

Rule file:

```text
deploy/prometheus/rules/ha-dr.yml
```

Перед выкладкой:

```bash
promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/ha-dr.yml
```

## 6. Пороговые значения

Текущие пороги — **bootstrap для испытаний, не SLA**:

| Сигнал | Порог |
|---|---|
| primary count | не равно 1 → critical |
| sync standby | < 1 более 2 мин → warning |
| replay lag | > 16 MiB более 2 мин → warning |
| DCS last seen | > 60 сек → critical |
| PgBouncer waiting clients | > 0 более 2 мин → warning |
| PgBouncer max wait | > 5 сек → critical |
| последний любой backup | > 8 ч → critical |
| последний full backup | > 8 суток → warning |

Порог backup выведен из стендового schedule первой партии (incremental каждые
6 часов, full раз в неделю) с небольшим grace period. После утверждения RPO/RTO
эти числа должны быть пересчитаны из SLA, а не оставлены как есть.

## 7. Grafana

Добавлены:

- datasource `Infrastructure Prometheus` (`uid=infra-prometheus`);
- provisioned dashboard `BZ GET — Stage 4 HA / DR`.

Dashboard показывает primary/sync standby, DCS freshness, replay lag,
PgBouncer waiting/maxwait и возраст backup.

В текущем Docker Compose datasource использует `http://prometheus:9090`.
Для production, если Grafana и Prometheus находятся не в одной внутренней сети,
адрес datasource должен быть адаптирован под реальную topology/PKI.

## 8. Alert delivery — честная граница

Prometheus rule evaluation **не равно доставке уведомлений**. В этой партии не
выбран корпоративный канал (email, Telegram, Mattermost, SMS, Service Desk) и
не добавлен production Alertmanager contact route, потому что адрес/получатели
не зафиксированы документацией Заказчика.

До production должны быть определены:

1. минимум два получателя/эскалационных уровня;
2. канал для `critical` и канал для `warning`;
3. окно подтверждения/эскалации;
4. тестовое synthetic alert с подтверждением доставки;
5. периодический тест канала уведомлений.

Без этого monitoring реализован, но operational alerting не считается
полностью принятым.

## 9. Приёмочный drill

Во время failover/DR испытаний первой партии должны одновременно наблюдаться:

1. `PatroniTargetDown` на остановленном leader;
2. кратковременное нарушение primary count с последующим возвратом к 1;
3. новый primary на dashboard;
4. отсутствие длительного `PatroniSyncStandbyMissing` после стабилизации;
5. PgBouncer без устойчивой очереди клиентов;
6. после failover — `pgbackrest_stanza_ok=1` и свежий backup/archive;
7. после restore drill — новый full backup и нормализация backup age.

Фактические timestamps alert firing/resolution включаются в журнал испытаний и
используются при расчёте реального RTO.
