# Этап 4 — monitoring HA/DR

Статус: **reference implementation / требуется стендовая проверка**.

Контур мониторит PostgreSQL/Patroni, PgBouncer, pgBackRest и сам Alertmanager.
Prometheus вычисляет правила, Grafana визуализирует состояние, Alertmanager
группирует/дедуплицирует и доставляет warning/critical notifications.

## Архитектура

```text
DB nodes
  Patroni :8008 /metrics ---------+
  postgres_exporter :9187 --------+----> Prometheus ----> Grafana
                                   |          |
App nodes                          |          +----> Alertmanager 1
  PgBouncer exporter :9127 -------+          +----> Alertmanager 2
                                   |                    |
Backup/ops node                    |              warning/critical
  pgBackRest exporter :9854 ------+                 webhooks
```

Все exporter/Alertmanager endpoints должны быть доступны только из
monitoring/operations сети.

## 1. Patroni

Patroni 4.1.x отдаёт Prometheus-метрики через `GET /metrics`; отдельный
exporter не требуется. Контролируются:

- ровно один `patroni_primary`;
- минимум одна `patroni_sync_standby` для выбранного baseline;
- `patroni_postgres_running`;
- свежесть `patroni_dcs_last_seen`;
- `patroni_failsafe_mode_is_active`;
- `patroni_pending_restart`;
- WAL/replay lag.

Правила — `deploy/prometheus/rules/ha-dr.yml`.

## 2. PostgreSQL exporter

На каждом DB-узле exporter работает под отдельной OS/DB-учёткой
`postgres_exporter`. Подключение локальное через Unix socket + peer auth.
SQL-роль получает `INHERIT`, `pg_monitor` и `CONNECT` к `bz_get`, но не
SUPERUSER/write privileges.

Проверка:

```bash
sudo -u postgres_exporter psql -h /var/run/postgresql -d bz_get -c 'select 1'
curl http://DB_MONITORING_IP:9187/metrics | grep '^pg_up'
```

Ожидается `pg_up 1`.

## 3. PgBouncer exporter

На каждом app-узле используется отдельный `pgbouncer_exporter`, включённый
только в `stats_users`, не `admin_users`. Для совместимости exporter'а в
PgBouncer включён `ignore_startup_parameters = extra_float_digits`.

Проверка:

```bash
curl http://APP_MONITORING_IP:9127/metrics | grep '^pgbouncer_'
```

Основные сигналы — waiting clients и max wait.

## 4. pgBackRest exporter

`deploy/monitoring/pgbackrest_exporter.py` — read-only exporter на Python
stdlib. Он запускает только `pgbackrest info --output=json`, кэширует результат
и отдаёт:

- exporter/stanza health;
- наличие WAL archive range;
- число backup;
- timestamp/age последних full/diff/incr/any backup.

При ошибке exporter остаётся доступен и выставляет `pgbackrest_exporter_up 0`.

Проверка:

```bash
curl http://BACKUP_MONITORING_IP:9854/-/healthy
curl http://BACKUP_MONITORING_IP:9854/metrics
```

## 5. Prometheus

Для HA-стенда используется
`deploy/prometheus/prometheus.stage4.yml.example`. Он:

- опрашивает Patroni, PostgreSQL/PgBouncer/pgBackRest exporters;
- опрашивает оба Alertmanager;
- отправляет каждый alert **сразу обоим** Alertmanager instances;
- загружает rule files из `/etc/prometheus/rules/*.yml`.

Не ставить единый load balancer между Prometheus и Alertmanager HA: это создало
бы отдельную точку отказа в доставке.

Перед выкладкой:

```bash
promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/ha-dr.yml
promtool check rules /etc/prometheus/rules/alert-delivery.yml
```

## 6. Bootstrap thresholds

Текущие пороги — **стендовые, не SLA**:

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
| Alertmanager target | down >1 мин → critical |
| Alertmanager cluster | <2 members >2 мин → warning |

После утверждения RPO/RTO значения должны быть пересчитаны из SLA.

## 7. Grafana

Provisioning уже содержит datasource `Infrastructure Prometheus` и dashboard
`BZ GET — Stage 4 HA / DR`. Dashboard показывает состояние primary/sync
standby, DCS freshness, replay lag, PgBouncer wait и возраст backup.

## 8. Alert delivery

Партия 3 добавляет `deploy/alertmanager/`:

- двухузловой Alertmanager HA;
- отдельные warning/critical routes;
- receiver URL через root-owned `url_file`, без секретов в Git;
- `send_resolved=true`;
- inhibition warning при наличии соответствующего critical;
- synthetic `test-alert.sh`;
- self-monitoring rules `deploy/prometheus/rules/alert-delivery.yml`.

Технический routing теперь существует, но фактический receiver остаётся
организационным параметром. До production Заказчик должен указать реальные
endpoint'ы/получателей и подтвердить synthetic warning + critical + resolved.

## 9. Приёмочный drill

Во время HA/DR испытаний должны наблюдаться одновременно:

1. `PatroniTargetDown` на остановленном DB leader;
2. кратковременное нарушение primary count и возврат к 1;
3. новый primary на dashboard;
4. отсутствие длительного `PatroniSyncStandbyMissing` после стабилизации;
5. отсутствие устойчивой очереди PgBouncer;
6. здоровый pgBackRest archive/backup после failover;
7. доставка warning/critical через Alertmanager при отказе одного peer;
8. resolved notification после восстановления;
9. фиксация timestamps firing/delivery/resolution для расчёта фактического RTO.

## Честная граница

Monitoring/Alertmanager configs в Git не доказывают работоспособность канала.
Operational alerting считается завершённым только после реального synthetic
delivery test с подтверждением получателя и проверки отказа одного
Alertmanager instance.
