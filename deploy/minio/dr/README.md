# Этап 4 — MinIO DR

Статус: **reference implementation / требуется стендовый failover+restore drill**.

Этот контур защищает два application-бакета отдельно от PostgreSQL DR:

- `bz-get-originals` — версии + Object Lock/WORM;
- `bz-get-working` — версии без Object Lock, допускает рабочие замены.

PostgreSQL backup не содержит бинарные объекты MinIO, поэтому восстановление
АИС считается полноценным только после проверки согласованной пары БД + S3.

## Почему bucket replication, а не site replication

Для первой production-схемы выбран **one-way active-passive bucket replication**
только для двух application-бакетов. Это сознательно уже, чем site replication:
мы не размножаем IAM/все бакеты/все настройки MinIO автоматически и сохраняем
явную точку управления failover.

Это уменьшает blast radius и риск split-brain. Обратная репликация никогда не
включается автоматически: после аварии сначала выбирается единственный active
site, старый site изолируется/rebuild'ится, затем направление репликации
настраивается заново от нового active к восстановленному passive.

## Целевая топология

```text
                    stable application endpoint
                   https://s3.bz-get.internal
                            |
                      DNS/LB control
                            |
              +-------------+-------------+
              |                           |
       PRIMARY MinIO                 DR MinIO
       distributed site              distributed site
       originals (WORM)  =========>  originals (WORM)
       working           =========>  working
              one-way server-side bucket replication
```

Обе площадки должны находиться в разных failure domains. Четыре каталога
`/data1..4` внутри одного dev-контейнера из `docker-compose.yml` не являются
DR и не используются как production topology.

## Предварительные требования

1. Одинаковая совместимая версия MinIO на primary и DR.
2. Отдельные distributed deployments и независимые диски/узлы.
3. TLS между площадками; `--insecure` в production запрещён.
4. Versioning включён на обоих бакетах обеих площадок.
5. `originals` на обеих площадках создан **с Object Lock** (`mc mb --with-lock`).
6. Dedicated replication credentials вместо root-ключей:
   - source: `replication-admin-policy.json`;
   - target: `replication-target-policy.json`.
7. Стабильный application endpoint должен переключаться операционно через
   DNS/LB; Django не должен менять имя бакета при DR.

Политики в репозитории используют текущие стандартные имена бакетов. Если
Заказчик меняет `MINIO_BUCKET_*`, JSON-политики должны быть изменены тем же
change request до применения.

## Инициализация DR-площадки

На DR-площадке создать бакеты ДО настройки replication:

```bash
mc mb --with-lock dr/bz-get-originals
mc mb --with-versioning dr/bz-get-working
```

`originals`, созданный без Object Lock, нельзя «доделать» в production как
эквивалент WORM-копии. `configure-replication.sh` в этом случае завершится
ошибкой fail-closed.

Создать dedicated users/policies стандартными средствами `mc admin policy` /
`mc admin user` и положить credentials в root-readable env-файл на operations
host. Реальные ключи в Git не хранятся.

## Настройка one-way replication

```bash
set -a
. /etc/bz-get/minio-dr.env
set +a
./deploy/minio/dr/configure-replication.sh
```

Скрипт:

- проверяет доступность обеих площадок;
- включает/проверяет Versioning;
- требует Object Lock capability на DR `originals`;
- добавляет ровно одно направление source -> target;
- включает перенос существующих объектов и delete/delete-marker semantics;
- не создаёт reverse rule.

После настройки:

```bash
./deploy/minio/dr/check-replication.sh
```

Проверка неразрушающая: `mc replicate status`, наличие правил и сравнение
количества видимых object versions. Равенство count — не доказательство
байт-в-байт равенства, поэтому приёмочный DR drill дополнительно сверяет
контрольные версии через `mc stat`/checksum.

## Object Lock и репликация `originals`

MinIO bucket replication переносит объектные версии и metadata, включая
retention/legal-hold semantics. Но DR-бакет обязан быть создан с Object Lock
изначально. Репликация delete/versioned-delete не должна обходить retention:
защищённая версия не может быть физически удалена до разрешённого срока.

Перед production нужно отдельно подтвердить выбранные Governance/Compliance
retention правила из основного WORM-регламента. Этот DR runbook не меняет их.

## Нормальная эксплуатация

Ежедневно/автоматизированно проверять:

```bash
mc replicate status source/bz-get-originals
mc replicate status source/bz-get-working
```

Минимум раз в квартал (или чаще по принятому регламенту) выполнять synthetic
DR drill на тестовом объекте в `working` и на специально созданном тестовом
WORM-объекте в отдельном тестовом bucket с теми же lock settings. Боевой
`originals` нельзя загрязнять служебными probe-файлами только ради мониторинга.

## Failover: потеря primary MinIO

Failover выполняется только оператором, а не автоматически по одиночному
healthcheck.

1. Объявить storage incident и зафиксировать время.
2. Остановить/заморозить writers: Django/Gunicorn и Celery задачи, которые
   загружают файлы.
3. Если primary ещё доступен — проверить replication backlog/status и время
   последней подтверждённой версии.
4. Изолировать неисправную primary-площадку от application endpoint.
5. Проверить DR `originals`/`working`, Object Lock и контрольные объекты.
6. Проверить согласованность с выбранной PostgreSQL recovery point.
7. Переключить `s3.bz-get.internal`/LB на DR site.
8. Только после этого разрешить writers.
9. Зафиксировать фактическую потерю данных (RPO) и время до восстановления
   приложения (RTO).

**Запрещено** одновременно оставлять обе площадки доступными для записи без
явно спроектированной active-active схемы. Эта партия такую схему не вводит.

## Failback / восстановление старой площадки

После ремонта старого primary:

1. Не возвращать его сразу в application DNS/LB.
2. Очистить/rebuild'ить его как passive target по утверждённой процедуре.
3. Создать бакеты с теми же Versioning/Object Lock capabilities.
4. Назначить новый active site как `MINIO_SOURCE_URL`, восстановленный — как
   `MINIO_TARGET_URL`.
5. Запустить `configure-replication.sh` и затем resync для существующих данных
   по штатным `mc replicate resync` средствам.
6. Дождаться завершения resync, сверить версии/контрольные checksums.
7. Выполнить planned cutback только отдельным change window либо оставить
   восстановленную площадку passive.

Нельзя включать reverse replication поверх старого несогласованного набора
данных до изоляции/rebuild — это создаёт конфликтующие версии и усложняет
определение корректной точки восстановления.

## Совместный PostgreSQL + MinIO restore drill

Минимальный приёмочный тест:

1. создать контрольную карточку НРД и загрузить `working` + `originals`;
2. дождаться PostgreSQL WAL archive и MinIO replication;
3. записать UUID документа, object keys, version IDs и checksum;
4. сделать последующее изменение `working`;
5. имитировать логическую ошибку/потерю primary site;
6. восстановить PostgreSQL в выбранную точку PITR;
7. выбрать соответствующее состояние MinIO на DR;
8. проверить, что ссылки из БД указывают на реально существующие версии;
9. проверить WORM/retention на `originals`;
10. запустить приложение в read-only validation, затем разрешить запись;
11. измерить фактические RPO/RTO.

Если БД восстановлена на время T, а объектные версии не соответствуют этой
точке, DR считается неуспешным даже если оба сервиса по отдельности healthy.

## RPO/RTO

Численные значения остаются **TBD до решения Заказчика**. Bucket replication
по умолчанию асинхронна, поэтому сам факт включённой репликации не означает
RPO=0. Реальное RPO определяется backlog/задержкой и измеряется на DR drill.

## Definition of Done

- DR deployment физически отделён от primary;
- versioning включён в обеих копиях обоих бакетов;
- Object Lock подтверждён на обеих копиях `originals`;
- root credentials не используются для постоянной replication;
- existing objects полностью resync'нуты;
- failover переключает стабильный application endpoint без смены bucket names;
- выполнен reverse resync после аварии без active-active split-brain;
- совместный PostgreSQL + MinIO restore drill воспроизводим вторым оператором;
- фактические RPO/RTO зафиксированы и сопоставлены с утверждённым SLA.
