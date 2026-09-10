# Этап 4 — стендовый acceptance cycle

Этот каталог переводит HA/DR Этапа 4 из набора конфигураций и runbook в
воспроизводимую процедуру приёмочных испытаний. Он **не создаёт production
инфраструктуру** и не запускает разрушительные действия из CI.

## Назначение

`acceptance-cycle.sh` запускается на выделенном operations/bastion host, который
имеет сетевой доступ к HA/DR стенду и установленные:

- `patronictl`;
- `etcdctl`;
- `pgbackrest`;
- `curl`;
- Python 3;
- MinIO Client (`mc`) — через существующий `deploy/minio/dr/check-replication.sh`.

Адреса стенда и пути к TLS-файлам задаются в root-readable
`/etc/bz-get/stage4-acceptance.env`. Шаблон: `acceptance.env.example`.

## Что проверяет preflight

До любого failover/PITR испытания harness требует:

1. ровно ожидаемые три Patroni member и ровно один leader/primary;
2. рабочие replicas и replication lag не выше стендового лимита;
3. здоровье всех трёх etcd endpoints через TLS;
4. успешный `pgbackrest check`, наличие backup и допустимый возраст последнего
   backup;
5. успешную проверку active-passive MinIO replication;
6. доступность обоих Alertmanager endpoints;
7. успешный health-check приложения через стабильный service endpoint.

Если любой пункт не проходит, acceptance cycle прекращается до инъекции отказа.

## Подготовка

```bash
sudo install -d -o root -g root -m 0750 /etc/bz-get
sudo install -o root -g root -m 0600 \
  deploy/acceptance/acceptance.env.example \
  /etc/bz-get/stage4-acceptance.env
```

Заполнить реальные адреса, TLS paths и MinIO DR env. Секреты и реальные
endpoints не коммитить в Git.

Установить harness:

```bash
sudo install -o root -g root -m 0755 \
  deploy/acceptance/acceptance-cycle.sh \
  /usr/local/sbin/bz-get-stage4-acceptance
```

## Старт испытания

У каждого запуска должен быть уникальный `RUN_ID`, например номер change/request:

```bash
sudo ACCEPTANCE_ENV=/etc/bz-get/stage4-acceptance.env \
  /usr/local/sbin/bz-get-stage4-acceptance CHG-2026-0042 preflight
```

Evidence создаётся в `ACCEPTANCE_EVIDENCE_ROOT/RUN_ID`.

## Рекомендуемый порядок acceptance

После успешного preflight оператор выполняет процедуры из
`deploy/dr/REBUILD_AND_DRILL.md` и фиксирует контрольные точки:

```bash
bz-get-stage4-acceptance CHG-2026-0042 checkpoint baseline-ready
bz-get-stage4-acceptance CHG-2026-0042 checkpoint planned-switchover-start
bz-get-stage4-acceptance CHG-2026-0042 checkpoint planned-switchover-complete
bz-get-stage4-acceptance CHG-2026-0042 checkpoint unplanned-failover-start
bz-get-stage4-acceptance CHG-2026-0042 checkpoint unplanned-failover-complete
bz-get-stage4-acceptance CHG-2026-0042 checkpoint pitr-start
bz-get-stage4-acceptance CHG-2026-0042 checkpoint pitr-validated
bz-get-stage4-acceptance CHG-2026-0042 checkpoint minio-failover-validated
bz-get-stage4-acceptance CHG-2026-0042 checkpoint alertmanager-peer-loss-validated
bz-get-stage4-acceptance CHG-2026-0042 checkpoint application-smoke-validated
```

Harness намеренно **не выполняет** `systemctl stop patroni`, promotion, DCS
cleanup, PITR target selection или MinIO DNS/LB switch. Эти действия остаются
change-controlled operator steps.

## Финализация и измеренные RPO/RTO

После полного цикла задаются реальные UTC timestamps и результаты подсистем:

```bash
export ACCEPTANCE_DB_RESULT=PASS
export ACCEPTANCE_MINIO_RESULT=PASS
export ACCEPTANCE_ALERT_RESULT=PASS
export ACCEPTANCE_APP_RESULT=PASS
export ACCEPTANCE_INCIDENT_UTC=2026-09-10T19:00:00Z
export ACCEPTANCE_LAST_DURABLE_UTC=2026-09-10T18:59:50Z
export ACCEPTANCE_SERVICE_RESTORED_UTC=2026-09-10T19:03:00Z

bz-get-stage4-acceptance CHG-2026-0042 finalize
```

`RESULT.md` содержит технический итог и измеренные `observed_rpo_seconds` /
`observed_rto_seconds`. Любой `FAIL` делает общий результат FAIL и возвращает
ненулевой exit code.

## Что считается PASS

Технический PASS допустим только когда одновременно подтверждены:

- PostgreSQL/Patroni switchover и аварийный failover;
- pgBackRest full/diff/incr + WAL и isolated PITR;
- pgBackRest-based rebuild одной replica после approval gate;
- MinIO failover/failback, версии объектов, checksums и WORM retention;
- доставка synthetic critical/warning alerts и работа при потере одного
  Alertmanager peer;
- Web/API health и выбранные бизнес-smoke tests после переключений;
- evidence содержит UTC timestamps, команды/логи и измеренные RPO/RTO.

Технический `RESULT.md` не заменяет подпись Заказчика/эксплуатации и не создаёт
SLA автоматически.

## CI

`validate.sh` использует fake Patroni/etcd/pgBackRest/Alertmanager/MinIO и
проверяет:

- happy-path preflight;
- запись checkpoints;
- расчёт RPO/RTO;
- общий PASS при PASS всех подсистем;
- fail-closed итог при FAIL любой подсистемы.

Это проверяет код harness, но не подменяет реальный стендовый acceptance.
