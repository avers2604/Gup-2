# Этап 4 — HA-кластер PostgreSQL

Статус: **bootstrap/reference implementation**. Конфигурации в этом каталоге
предназначены для сборки и приёмочных испытаний HA-контура, но не считаются
готовыми к промышленной эксплуатации до прохождения чек-листа ниже и
утверждения Заказчиком RPO/RTO, адресного плана, PKI и эксплуатационных ролей.

## Целевая топология первой партии

```text
Web/API node A                         Web/API node B
Django                                Django
  |                                     |
127.0.0.1:6432                        127.0.0.1:6432
PgBouncer                             PgBouncer
  |                                     |
127.0.0.1:6433                        127.0.0.1:6433
HAProxy                               HAProxy
  |  Patroni GET /primary               |  Patroni GET /primary
  +---------------+---------------------+
                  |
        +---------+---------+
        |         |         |
       db1       db2       db3
     Patroni   Patroni   Patroni
    Postgres  Postgres  Postgres
        |         |         |
        +---- streaming ----+

        etcd1 --- etcd2 --- etcd3
             quorum / DCS

        pgBackRest -> отдельный backup host
```

Три узла etcd используются намеренно: DCS для автоматического failover должен
иметь нечётный кворум. Три PostgreSQL-узла дают primary + две реплики и
позволяют сохранить работоспособность записи при отказе одного DB-узла при
выбранном baseline `synchronous_node_count=1`.

PgBouncer и HAProxy запускаются **на каждом узле приложения**, а Django
подключается только к loopback. Это убирает отдельный центральный DB-router как
новую единую точку отказа. Если само приложение развёрнуто только на одном
сервере, HA базы не делает Web/API слой высокодоступным — это отдельная задача.

## Файлы

- `patroni.yml.example` — конфигурация Patroni/PostgreSQL одного DB-узла;
- `etcd.env.example` — переменные одного члена трёхузлового etcd;
- `haproxy.cfg.example` — локальный маршрутизатор на текущий Patroni primary;
- `pgbouncer.ini.example` — локальный пул соединений приложения;
- `validate.sh` — неразрушающая проверка реальных HA-конфигов;
- `../dr/pgbackrest.conf.example` — архивирование WAL и backup repository;
- `../dr/README.md` — DR-регламент и порядок восстановления.

Все `CHANGE_ME_*` значения должны заменяться при развёртывании. Пароли,
приватные ключи и реальные backup cipher passphrase в Git не хранятся.

## Порядок развёртывания

### 1. Подготовить сеть и PKI

Нужны отдельные приватные адреса для `db1..db3` и `etcd1..etcd3`.
Межузловые порты:

- etcd clients: TCP 2379;
- etcd peers: TCP 2380;
- PostgreSQL: TCP 5432;
- Patroni REST: TCP 8008;
- SSH к backup host — только между PostgreSQL/операционным контуром и
  сервером pgBackRest.

PostgreSQL и etcd не публикуются в пользовательскую сеть. Примеры требуют TLS
для etcd и удалённых PostgreSQL-соединений. Реальные сертификаты должны иметь
SAN с адресами/DNS-именами соответствующих узлов.

### 2. Поднять etcd-кворум

На каждом etcd-узле установить одинаковую версию etcd, скопировать
`etcd.env.example`, заменить node-local параметры и запустить сервис.

Проверка с административного хоста:

```bash
ETCDCTL_API=3 etcdctl \
  --endpoints=https://etcd1:2379,https://etcd2:2379,https://etcd3:2379 \
  --cacert=/etc/bz-get/pki/ca.crt \
  --cert=/etc/bz-get/pki/operator-etcd.crt \
  --key=/etc/bz-get/pki/operator-etcd.key \
  endpoint health --cluster
```

До запуска Patroni все три endpoint должны быть healthy. Один недоступный узел
не должен лишать etcd кворума; два — должны.

### 3. Подготовить pgBackRest и backup repository

На всех DB-узлах установить pgBackRest и разместить
`../dr/pgbackrest.conf.example`. На отдельном backup host подготовить репозиторий
и SSH-доступ от системного пользователя postgres/pgbackrest по принятой в
организации схеме ключей.

На этом шаге проверяется конфигурация файлов, сеть, SSH/TLS и права каталогов,
но **`stanza-create`/`pgbackrest check` ещё не выполняются**: им нужен уже
работающий PostgreSQL primary. `archive_command` заранее включён в
`patroni.yml.example`, поэтому после появления первого leader stanza нужно
создать сразу, не откладывая до подключения реплик.

### 4. Bootstrap первого Patroni leader

На `db1` заполнить `patroni.yml.example` и запустить Patroni. Первый узел,
получивший initialize lock в etcd, создаст кластер.

Сразу после появления работающего primary создать stanza и проверить архив:

```bash
sudo -u postgres pgbackrest --stanza=bz-get stanza-create
sudo -u postgres pgbackrest --stanza=bz-get check
sudo -u postgres pgbackrest --stanza=bz-get --type=full backup
sudo -u postgres pgbackrest --stanza=bz-get info
```

До успешного `check` и первого full backup кластер считается bootstrap-стендом,
а не готовым HA/DR-контуром. Ошибки `archive-push`, возникшие между первым
стартом PostgreSQL и созданием stanza, должны исчезнуть после `stanza-create`;
перед продолжением убедиться, что WAL архивируется штатно.

### 5. Подключить db2 и db3 как реплики

Последовательно запустить Patroni на `db2` и `db3`, используя тот же `scope` и
DCS. Проверка:

```bash
patronictl -c /etc/patroni/patroni.yml list
```

Ожидается один Leader и две Replica без заметного replication lag. Кластер
инициализируется с data checksums, а `wal_log_hints=on`; оба механизма дают
Patroni возможность использовать `pg_rewind` при возврате бывшего primary.

### 6. Поднять локальный HAProxy и PgBouncer на каждом app-узле

HAProxy слушает только `127.0.0.1:6433` и направляет TCP-трафик на PostgreSQL
того DB-узла, чей Patroni REST отвечает `200` на `/primary`.
PgBouncer слушает `127.0.0.1:6432` и направляет server connections на HAProxy.

Для Django в HA-среде:

```dotenv
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=6432
```

Приложение не должно знать, какой DB-узел сейчас primary.

### 7. Выполнить неразрушающую проверку конфигов

После замены всех placeholders на каждом соответствующем узле:

```bash
PATRONI_CONFIG=/etc/patroni/patroni.yml \
HAPROXY_CONFIG=/etc/haproxy/haproxy.cfg \
PGBOUNCER_CONFIG=/etc/pgbouncer/pgbouncer.ini \
./deploy/ha/validate.sh
```

Скрипт проверяет Patroni schema/GUC, HAProxy syntax и обязательный проектный
контракт PgBouncer, не выполняя failover и не меняя данные.

## Режим синхронной репликации

Bootstrap задаёт:

```yaml
synchronous_mode: on
synchronous_mode_strict: false
synchronous_node_count: 1
```

Это выбранный **технический baseline**, а не утверждённый SLA. При доступной
синхронной реплике подтверждённые транзакции могут автоматически failover'иться
без потери подтверждённых клиенту изменений. `strict=false` сознательно
оставляет primary возможность писать, когда подходящей sync-реплики временно
нет; в таком режиме при последующем аварийном отказе нулевая потеря данных уже
не гарантируется.

До production Заказчик должен выбрать приоритет:

- **durability first** — включить `synchronous_mode_strict=true`, принимая
  возможную недоступность записи при потере sync-реплики;
- **availability first** — оставить `strict=false` и принять ненулевой риск
  потери последних транзакций в редком двойном отказе.

Без этого решения нельзя честно объявлять RPO=0.

## Planned switchover

Перед обслуживанием primary:

```bash
patronictl -c /etc/patroni/patroni.yml list
patronictl -c /etc/patroni/patroni.yml switchover
```

После выбора нового leader проверить:

```sql
SELECT pg_is_in_recovery();
```

На новом primary ожидается `false`; на репликах — `true`.
На каждом app-узле новый leader должен автоматически стать единственным healthy
backend HAProxy, без изменения `.env` Django.

## Аварийный failover — приёмочный сценарий

1. Убедиться, что все три DB-узла синхронизированы и backup/WAL archive healthy.
2. Зафиксировать тестовую запись в приложении.
3. Жёстко остановить Patroni/PostgreSQL на текущем leader.
4. Измерить время до появления нового leader в `patronictl list`.
5. Проверить Web/API через обычный endpoint приложения — без смены настроек.
6. Проверить наличие тестовой записи.
7. Вернуть старый узел и убедиться, что он присоединился как replica (через
   `pg_rewind` или повторное клонирование, если rewind невозможен).
8. Проверить `pgbackrest check` и непрерывность архива WAL после failover.

Результат теста фиксируется в журнале испытаний: времена начала/восстановления,
новый leader, lag, наличие тестовой транзакции и способ возврата старого узла.

## Запрещённые сокращения

- Не запускать production etcd в одноузловом режиме из текущего
  `docker-compose.yml` — это dev-конфигурация, не HA.
- Не указывать `0.0.0.0/0` в `pg_hba` для приложения/репликации.
- Не хранить пароли Patroni/PostgreSQL, TLS private keys и pgBackRest cipher
  passphrase в репозитории.
- Не считать наличие реплики резервной копией: логическая/операторская ошибка
  реплицируется мгновенно; DR опирается на отдельный pgBackRest repository.
- Не объявлять RPO/RTO выполненными до измеренного restore/failover drill.

## Definition of Done для HA-подчасти Этапа 4

- 3/3 etcd healthy, потеря одного узла не останавливает DCS;
- 3 PostgreSQL/Patroni узла: один leader, две streaming replicas;
- Django работает только через локальный PgBouncer/HAProxy;
- автоматический failover одного DB-узла проходит без ручной смены endpoint;
- бывший primary штатно возвращается replica;
- WAL непрерывно уходит в отдельный pgBackRest repository;
- выполнен хотя бы один planned switchover и один unplanned failover drill;
- фактические RTO/RPO из испытаний записаны и сопоставлены с утверждёнными
  требованиями Заказчика.
