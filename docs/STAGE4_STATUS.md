# Этап 4 — статус после перепроверки репозитория

Дата перепроверки: 2026-09-10.

Этот файл фиксирует именно состояние репозитория, а не желаемую архитектуру.
Нужен, чтобы README/STACK и инфраструктурные шаблоны не воспринимались как
доказательство уже проведённых HA/DR испытаний.

## Что было в `main` до начала Этапа 4

Уже присутствовало:

- одиночный PostgreSQL 18.6 в `docker-compose.yml`;
- PgBouncer 1.21.0, но приложение в dev подключалось к PostgreSQL напрямую;
- одноузловой etcd v3.5.17 — только dev-заготовка для будущего Patroni;
- MinIO, Redis, ClamAV, Prometheus, Grafana;
- `STACK.md` с выбранными версиями Patroni 4.1.5 и pgBackRest 2.59.x;
- Prometheus self-scrape без реального мониторинга Patroni/PostgreSQL;
- README с честной пометкой, что HA/DR — ещё «дальше по плану».

Не было:

- Patroni-конфигурации;
- трёхузлового etcd production/reference-контура;
- механизма маршрутизации приложения на текущий primary;
- pgBackRest-конфигурации;
- backup/restore/PITR runbook;
- утверждённых RPO/RTO;
- отработанного failover/restore drill;
- DR-схемы MinIO.

## Что добавляет первая партия Этапа 4

`deploy/ha/`:

- `patroni.yml.example` — 3-node PostgreSQL/Patroni baseline;
- `etcd.env.example` — 3-node etcd/TLS baseline;
- `haproxy.cfg.example` — определение current primary через Patroni REST;
- `pgbouncer.ini.example` — локальный transaction pool приложения;
- `README.md` — порядок bootstrap, switchover/failover и DoD.

`deploy/dr/`:

- `pgbackrest.conf.example` — WAL archive + encrypted remote repository;
- `README.md` — DR/PITR runbook, restore drill и форма требований RPO/RTO.

`.env.example` теперь явно разделяет dev-подключение `localhost:5432` и HA
production endpoint `127.0.0.1:6432` через локальные PgBouncer/HAProxy.

## Документационные расхождения, найденные при аудите

### 1. README начинается как будто репозиторий всё ещё только «каркас Этапа 1»

Это исторически устаревшая формулировка: в репозитории уже есть существенная
реализация Этапов 2–3 и открыта следующая партия рабочего места НРД. При
следующем общем редактировании README заголовочную формулировку нужно заменить
на нейтральное описание текущего состояния, а не номера одного этапа.

### 2. `STACK.md` помечает Patroni и pgBackRest как «Этап 4, вне репозитория»

После merge этой партии это станет неверно: reference configs и runbook уже
будут в `deploy/ha`/`deploy/dr`. Само наличие файлов, однако, не означает, что
кластер развёрнут или испытан.

### 3. `docker-compose.yml` нельзя называть HA-контуром

Один etcd и один PostgreSQL в Compose остаются **dev-средой**. Их не следует
«размножать» и выдавать за production Patroni cluster: для HA нужны отдельные
failure domains, сетевой/PKI план и реальные испытания потери узла.

### 4. MinIO в Compose — не DR

Четыре data directory в одном контейнере/на одном host не защищают от потери
узла. PostgreSQL DR и MinIO DR должны испытываться совместно, потому что БД
хранит метаданные, а файлы находятся в S3-совместимом хранилище.

### 5. Monitoring Этапа 4 ещё не завершён

`deploy/prometheus/prometheus.yml` пока мониторит только сам Prometheus. В
следующей партии нужно добавить Patroni `/metrics`, PostgreSQL exporter,
PgBouncer exporter/метрики, freshness pgBackRest backup/WAL и алерты на
отсутствие quorum/replica/backup.

## Честная граница этой партии

Первая партия **создаёт проверяемую конфигурационную основу и регламент**, но
не может доказать RPO/RTO без реальных серверов и отказных испытаний.

До закрытия Этапа 4 обязательно нужны как минимум:

1. развёртывание 3x etcd + 3x Patroni/PostgreSQL на стенде;
2. автоматический failover drill;
3. planned switchover drill;
4. pgBackRest full/diff/incr + continuous WAL;
5. PITR на отдельный recovery host;
6. восстановление после потери всего тестового DB-кластера;
7. DR для MinIO;
8. Prometheus/Grafana monitoring;
9. утверждённые Заказчиком RPO/RTO и подтверждение измерениями.
