# Этап 4 — DR-регламент PostgreSQL

Статус: **reference implementation / требуется стендовая приёмка**.

Этот документ задаёт общий DR-контур PostgreSQL. Специализированные процедуры:

- `deploy/minio/dr/README.md` — MinIO active-passive DR, WORM и resync;
- `deploy/dr/REBUILD_AND_DRILL.md` — guarded Patroni replica rebuild из
  pgBackRest и объединённый HA/DR drill;
- `deploy/monitoring/README.md` — HA/DR monitoring;
- `deploy/alertmanager/README.md` — доставка аварийных уведомлений.

Наличие этих файлов в Git не подтверждает SLA: RPO/RTO должны быть измерены на
реальной целевой инфраструктуре.

## 1. Цели восстановления

Перед промышленной эксплуатацией Заказчик должен утвердить:

| Параметр | Значение | Статус |
|---|---:|---|
| RPO — допустимая потеря данных | **TBD** | требуется решение Заказчика |
| RTO — допустимое время восстановления | **TBD** | требуется решение Заказчика |
| Retention полных backup | bootstrap: 4 | требует утверждения |
| Retention differential backup | bootstrap: 14 | требует утверждения |
| Срок хранения истории manifest | bootstrap: 365 дней | требует утверждения |
| Отдельный backup failure domain | **TBD** | требуется адресный план |

Bootstrap-значения нужны только для испытаний и не являются юридической или
эксплуатационной политикой хранения.

## 2. Что защищает каждый механизм

- **Patroni + streaming replication** — отказ одного PostgreSQL-узла;
- **etcd quorum** — согласованный выбор leader и защита от split-brain;
- **PgBouncer + HAProxy** — стабильный DB endpoint приложения;
- **pgBackRest + WAL archive** — логическая ошибка, потеря кластера, PITR;
- **MinIO bucket replication** — отдельный active-passive DR для S3 objects;
- **Alertmanager HA** — доставка HA/DR alerts в утверждённые каналы.

Реплика не является backup: логическая ошибка или ошибочная миграция также
реплицируются на standby.

## 3. Backup repository

Repository pgBackRest должен находиться в другом failure domain от `db1..db3`.
Backup шифруется, а cipher passphrase хранится отдельно от Git и production
репозитория. Потеря ключа означает потерю возможности восстановить backup.

Конфигурация reference находится в `pgbackrest.conf.example`.

## 4. Инициализация pgBackRest

На всех DB-узлах заранее готовятся pgBackRest, каталоги spool/log и доступ к
repository. `stanza-create` выполняется после появления первого Patroni primary:

```bash
sudo -u postgres pgbackrest --stanza=bz-get stanza-create
sudo -u postgres pgbackrest --stanza=bz-get check
sudo -u postgres pgbackrest --stanza=bz-get --type=full backup
sudo -u postgres pgbackrest --stanza=bz-get info
```

`patroni.yml.example` включает непрерывный `archive_command` и
`archive_timeout=60s`. Это не обещает RPO=60 секунд: фактический RPO зависит от
успешности archive-push, repository, сети и согласованной MinIO object version.

## 5. Bootstrap schedule для стенда

До утверждения SLA:

- full — раз в неделю;
- differential — ежедневно;
- incremental — каждые 6 часов;
- WAL — непрерывно через `archive_command`.

После каждого backup контролируются exit code, `pgbackrest info`, freshness
metrics и WAL archive.

## 6. Ежедневная неразрушающая проверка

```bash
sudo -u postgres pgbackrest --stanza=bz-get check
sudo -u postgres pgbackrest --stanza=bz-get info
patronictl -c /etc/patroni/patroni.yml list
```

Проверяются один leader, ожидаемые replicas, отсутствие растущего replication
lag, свежий backup/WAL и доступность независимого repository.

## 7. Сценарий A — отказ одного DB-узла

Это HA, а не PITR.

1. Подтвердить новый leader через `patronictl list`.
2. Проверить Web/API через обычный адрес приложения.
3. Проверить HAProxy/PgBouncer routing.
4. Не выполнять PITR при здоровом leader.
5. Вернуть отказавший узел как replica через `pg_rewind`, basebackup или
   guarded pgBackRest rebuild.
6. Проверить streaming/sync standby, lag и `pgbackrest check`.

Автоматизированный replica rebuild описан в `REBUILD_AND_DRILL.md`. Он никогда
не должен применяться к leader/primary.

## 8. Сценарий B — логическая ошибка / ошибочная миграция

Failover не помогает, если ошибка уже реплицировалась. Нужен PITR.

### 8.1 Зафиксировать recovery point

Определить UTC timestamp **до** ошибочной операции и источник времени:
WORM-аудит, журнал изменения или change/incident record.

### 8.2 Остановить запись

- перевести приложение в maintenance/read-only;
- остановить writers Django/Gunicorn;
- остановить Celery tasks, которые пишут данные;
- исключить автоматический restart PostgreSQL во время isolated restore.

### 8.3 Сохранить исходное состояние

Не начинать recovery с безусловного `rm -rf`. При возможности сохранить
повреждённый PGDATA/диски как forensic copy.

### 8.4 PITR на isolated recovery host

На совместимом recovery host:

```bash
sudo -u postgres pgbackrest \
  --stanza=bz-get \
  --type=time \
  --target='CHANGE_ME_UTC_TIMESTAMP' \
  --target-action=promote \
  restore
```

`target-action=promote` допустим здесь, потому что это изолированный DR primary,
а не replica rebuild. До переключения приложения обязательно проверить
бизнес-данные, схему, audit trail и согласованные MinIO object versions.

После подтверждённого ручного PITR можно подписать combined drill как PASS и
через `approve-pgbackrest-rebuild.sh` разрешить автоматизированный pgBackRest
path для **реплик** Patroni.

## 9. Сценарий C — потеря всего PostgreSQL-кластера

1. Объявить DR-инцидент и остановить writers.
2. Изолировать старые DB-узлы/DCS от возможности вернуть старый primary.
3. Восстановить выбранную точку на isolated recovery host.
4. Провести бизнес-валидацию и сверку MinIO object versions.
5. Только после валидации сформировать новый Patroni cluster identity/DCS state.
6. Добавить replicas; для них после approval допустим pgBackRest rebuild.
7. Переключить HAProxy/PgBouncer на новый кластер.
8. Поднять приложение ограниченно, затем полностью.
9. Выполнить новый full backup после стабилизации.

Партия 4 **не автоматизирует** удаление DCS, выбор PITR target или создание
нового primary. Эти действия остаются change-controlled из-за риска split-brain
и необратимой потери данных.

## 10. MinIO / файловое хранилище

MinIO DR реализован отдельной партией 3 в `deploy/minio/dr/`:

- one-way active-passive bucket replication;
- `originals` с Object Lock/WORM на обеих площадках;
- `working` с Versioning;
- least-privilege replication credentials;
- controlled failover/failback/resync;
- stable application S3 endpoint.

PostgreSQL recovery считается успешным для всей АИС только если выбранной точке
БД соответствуют реально существующие object versions MinIO.

## 11. Объединённый restore/failover drill

`combined-drill.sh` собирает evidence вокруг операторских действий, но не
выполняет разрушительные операции сам.

Минимальная последовательность:

1. `combined-drill.sh preflight`;
2. создать контрольный документ/файлы и дождаться WAL + MinIO replication;
3. planned switchover;
4. unplanned leader loss;
5. rebuild одной replica;
6. тестовая логическая ошибка + isolated PITR;
7. MinIO failover/failback/resync;
8. synthetic warning/critical + отказ одного Alertmanager;
9. application/business validation;
10. заполнить incident/last durable/service restored UTC;
11. `combined-drill.sh finish`;
12. второй оператор повторяет процедуру по runbook.

Детальный сценарий и PASS criteria находятся в `REBUILD_AND_DRILL.md`.

## 12. Что фиксировать по каждому DR-тесту

- drill/change/incident ID;
- тип отказа;
- backup set и PITR target;
- последний подтверждённый WAL;
- MinIO object keys/version IDs/checksums;
- WORM/retention состояние `originals`;
- время объявления инцидента;
- время выбора нового leader;
- время готовности БД/объектов/приложения;
- observed RPO upper bound;
- observed RTO;
- alerts firing/resolved/delivery timestamps;
- ошибки, ручные обходы и корректирующие действия;
- оператор/approver;
- PASS/FAIL.

## 13. Definition of Done DR-подчасти Этапа 4

- backup repository физически отделён от PostgreSQL-кластера;
- full/diff/incr backup и непрерывный WAL проверены;
- выполнен isolated PITR;
- выполнен planned и unplanned Patroni failover;
- MinIO DR реально испытан с Object Lock и resync;
- critical notification доставляется при отказе одного Alertmanager;
- после подписанного PASS испытан pgBackRest-based rebuild одной replica;
- combined drill повторён вторым оператором;
- фактические RPO/RTO записаны и утверждены;
- bootstrap alert thresholds заменены на SLA-derived значения.
