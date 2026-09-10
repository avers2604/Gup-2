# АИС «БЗ ГЭТ»

База знаний нормативно-распорядительной документации и типовых бланков СПб ГУП «Горэлектротранс» по ТЗ-БЗ-ГЭТ-2026-V2.2 и дополнению к нему.

Проект реализован как модульный монолит на Django. Репозиторий уже содержит не только доменную модель, но и Web GUI, External API, IAM/2FA, Smart Search, OCR-конвейер, контроль загрузок, асинхронные фоновые задачи, а также reference-контур HA/DR с Patroni, pgBackRest, MinIO DR, Prometheus/Grafana, Alertmanager и стендовым acceptance tooling.

> **Важно:** HA/DR-код и эксплуатационные шаблоны находятся в репозитории, но Этап 4 нельзя считать эксплуатационно принятым до реального стендового failover/PITR/MinIO drill, измерения RPO/RTO и утверждения результатов Заказчиком.

## Текущий статус

Состояние на 10.09.2026:

| Этап | Состояние | Что входит |
|---|---|---|
| **Этап 1** | реализован | доменная модель НРД, история статусов, граф версий, банк бланков, IAM, WORM-аудит, дизайн-система |
| **Этап 2** | реализован | Web GUI и DRF/JWT API, 2FA, Smart Search; рабочие места НРД (реестр, карточка, запись, граф связей), банка бланков, журнала аудита и личный кабинет |
| **Этап 3** | базовый контур реализован | Celery/Redis, OCR Tesseract + OpenCV, антивирус ClamAV, запрет макросов/ActiveX, асинхронный импорт персонала |
| **Этап 4** | функционально реализован, стендовая приёмка не завершена | Patroni/etcd HA, PgBouncer/HAProxy, pgBackRest/PITR, MinIO DR, monitoring/alerting, guarded replica rebuild, combined DR drill и acceptance harness |

Подробный фактический статус Этапа 4: [`docs/STAGE4_STATUS.md`](docs/STAGE4_STATUS.md).

## Основные возможности

### Нормативно-распорядительная документация

- карточка НРД с реквизитами ТЗ;
- граф связей версий/замены документов;
- темпоральная история статусов SCD-2 на PostgreSQL `tstzrange`;
- ограничение пересекающихся периодов через `EXCLUDE USING gist`;
- категории хранения и автоматический расчёт retention policy;
- отдельный признак допуска к документам «ДСП»;
- WORM-аудит юридически и эксплуатационно значимых действий;
- Web-реестр `/documents/` с фильтрами и pagination;
- карточка `/documents/<uuid>/` с реквизитами, связями, историей статусов, файлами и retention metadata;
- Web-операции записи: регистрация карточки, правка черновика, смена статуса по
  графу допустимых переходов с проверкой ацикличности графа связей при публикации;
- ведение SCD-2 истории статусов на любом пути записи, включая Django admin и импорт;
- Web-управление графом связей версионности с проверкой ацикличности и
  фиксацией добавления и снятия ребра в WORM-журнале.

Web-операции записи (регистрация, правка черновика, смена статуса) выполняются в рабочем месте НРД; статус меняется по явному графу переходов с записью в WORM-журнал и ведением SCD-2. В Django admin остаются справочники и операции, до которых Web GUI ещё не дошёл.

### Банк бланков

`apps/templates_bank/` хранит семейства форм и версии бланков с классификацией изменений. Файлы проходят тот же контур контроля загрузок, что и НРД.

Рабочее место `/templates/` даёт список семейств с действующей версией, линию версий и скачивание. Выпущенная версия неизменяема (ТЗ 4.3.1 требует инкремента версии даже для минорной корректировки): публикуется новая, предыдущая переводится в архив и связывается с ней в обе стороны. Скачивания учитываются, выдача архивной формы попадает в WORM-журнал.

### IAM, аутентификация и безопасность

Внутренний IAM реализован без обязательной зависимости от AD/LDAP.

Основные механизмы:

- пользователи идентифицируются по табельному номеру;
- ролевая модель и отдельный флаг допуска «ДСП»;
- Argon2 для паролей;
- парольная политика: минимум 14 символов, история 10 паролей, срок действия 365 дней;
- TOTP/2FA, обязательная для администратора;
- TOTP secret хранится в зашифрованном виде;
- административный сброс 2FA с аудитом;
- JWT access/refresh + blacklist для API;
- принудительная смена истёкшего/сброшенного пароля;
- lockout после 5 неудачных попыток за 15 минут по табельному номеру;
- дополнительный контур защиты от перебора по IP;
- HTTP throttling для auth/search API;
- таймауты Web-сессии;
- доменные события для критических IAM side effects и WORM-аудита.

IAM application services разделены по назначению:

```text
apps/iam/auth_service.py       аутентификация, lockout, TOTP, session audit
apps/iam/personnel_service.py  импорт персонала и отчёты
apps/iam/services.py           compatibility facade
```

Импорт персонала поддерживает синхронный и асинхронный режимы. В async-режиме Excel сначала помещается в рабочее storage, а в Celery передаётся только имя объекта и ID оператора — бинарный файл не передаётся через Redis.

## Smart Search

Поиск находится в `apps/search_ocr/` и использует PostgreSQL Full Text Search с русской конфигурацией и тезаурусом.

Формула ранжирования:

```text
score = ExactMatch(reg_number) * 1.0
      + FTS(title)             * 0.8
      + FTS(summary)           * 0.5
      + FTS(ocr_body)          * 0.2
```

Реализованы:

- расширение запроса по VERIFIED-записям тезауруса;
- разрешение неоднозначных аббревиатур;
- отдельная persisted read-model `DocumentSearchIndex`;
- сохранённые `tsvector` для title/summary/OCR;
- GIN-backed candidate selection;
- асинхронное обновление индекса после изменения документа;
- полный rebuild через management command;
- фильтрация документов «ДСП» по допуску пользователя;
- pagination в Web и API;
- ограничение поисковой строки: до 200 символов и до 12 слов;
- throttling search API.

Интерфейсы:

```text
GET /                             Web Smart Search
GET /api/v1/search/documents/     External API поиска
```

Web-поиск выводит по 20 результатов на страницу. API использует ту же бизнес-логику и paginated JSON-ответ.

## OCR и контроль загрузок

OCR-конвейер работает асинхронно через Celery + Redis.

Пайплайн:

```text
загрузка файла
  -> ClamAV / macro check
  -> сохранение
  -> transaction.on_commit()
  -> Celery
  -> PDF rasterization
  -> OpenCV preprocessing
  -> Tesseract OCR
  -> ocr_body / confidence / status
  -> обновление поискового read-model
```

Поддерживаются:

- пакетная растеризация PDF;
- deskew, denoise и бинаризация изображения;
- русский Tesseract OCR;
- дифференцированные пороги качества по категории документа;
- retry и timeout Celery-задач;
- WORM-аудит успеха/ошибки OCR;
- ретроактивный повтор OCR management-командой;
- синхронная fail-closed проверка файлов через ClamAV до записи в storage;
- структурная проверка DOCX/XLSX на макросы и ActiveX.

Известные границы OCR: нет пользовательского UI прогресса очереди; Tesseract не даёт Top-3 альтернатив распознавания; для схем/чертежей пока распознаётся страница целиком, а не только штамп.

## HTTP-контуры

В проекте разделены Web GUI и External API.

| URL | Назначение |
|---|---|
| `/` | Smart Search |
| `/documents/` | реестр НРД |
| `/documents/<uuid>/` | карточка НРД |
| `/documents/new/` | регистрация карточки НРД (черновик) |
| `/documents/<uuid>/edit/` | правка черновика |
| `/documents/<uuid>/status/` | смена статуса (публикация, отмена, архив) |
| `/documents/<uuid>/relations/new/` | связь версионности (ТЗ 4.2.2) |
| `/templates/` | банк бланков: семейства форм |
| `/templates/<uuid>/` | линия версий бланка, выпуск и скачивание |
| `/audit/` | журнал аудита (Офицер ИБ, Администратор) |
| `/accounts/profile/` | личный кабинет |
| `/accounts/` | Web login/TOTP/password flow |
| `/api/v1/auth/` | JWT auth API |
| `/api/v1/search/documents/` | поиск НРД через API |
| `/api/v1/schema/` | OpenAPI schema |
| `/api/v1/docs/` | Swagger UI |
| `/health/` | readiness приложения + БД (`200 healthy` / `503 unhealthy`) |
| `/admin/` | Django admin |
| `/styleguide/` | живой каталог компонентов дизайн-системы |

`/health/` выполняет минимальный `SELECT 1` через настроенный DB endpoint, не кэшируется и не раскрывает детали инфраструктурной ошибки.

## Архитектура

### Приложение

```text
Browser / Integration
        |
        +--> Web GUI: Django Templates
        |
        +--> External API: Django REST Framework + JWT
                         |
                    application services
                         |
      +------------------+------------------+
      |                  |                  |
   documents            iam             search_ocr
      |                  |                  |
      +------------------+------------------+
                         |
                 PostgreSQL / S3 / Redis
```

Проект остаётся модульным монолитом: домены разделены Django-приложениями, а тяжёлые/долгие операции выносятся в Celery.

### Production DB HA

Reference-топология Этапа 4:

```text
Django
  -> local PgBouncer :6432
  -> local HAProxy   :6433
  -> current Patroni primary :5432

Patroni db1/db2/db3
  -> etcd1/etcd2/etcd3 quorum over TLS
```

HAProxy определяет writable primary через Patroni REST API. PgBouncer и HAProxy предполагаются локальными на каждом app-узле, чтобы не создавать отдельный центральный DB-router как SPOF.

Dev `docker-compose.yml` не является HA-кластером и не должен использоваться как доказательство отказоустойчивости.

## Этап 4: HA, DR и мониторинг

В `main` уже находятся пять партий Этапа 4.

### 1. PostgreSQL HA + pgBackRest DR

`deploy/ha/` и `deploy/dr/` содержат:

- 3-node Patroni/PostgreSQL reference configuration;
- 3-node etcd/TLS quorum;
- synchronous replication baseline;
- `pg_rewind`, timeline checks и data checksums;
- PgBouncer + HAProxy routing;
- pgBackRest repository configuration;
- continuous WAL archive;
- full/diff/incr backup;
- PITR и full-cluster recovery runbook.

### 2. Monitoring

Добавлены:

- Patroni native `/metrics`;
- `postgres_exporter`;
- `pgbouncer_exporter`;
- read-only pgBackRest exporter;
- Prometheus scrape/rules;
- Grafana dashboard `BZ GET — Stage 4 HA / DR`;
- алерты на primary/sync standby/DCS/replication lag/PgBouncer/backup freshness.

Текущие пороги — bootstrap для стенда, а не утверждённый SLA.

### 3. MinIO DR + Alertmanager

MinIO работает по active-passive DR-схеме для двух application buckets:

```text
active site                       passive DR site
bz-get-originals (WORM)  ----->   bz-get-originals (WORM)
bz-get-working           ----->   bz-get-working
```

Для `originals` обязательны Versioning и Object Lock/WORM на обеих площадках. Репликация выполняется отдельными least-privilege пользователями.

Alertmanager разворачивается в двухузловом HA-контуре; Prometheus отправляет alerts обоим peers. `warning` и `critical` маршрутизируются отдельно, а реальные receiver URL хранятся вне Git.

### 4. Guarded Patroni replica rebuild

Patroni поддерживает порядок создания replica:

```text
pgBackRest -> pg_basebackup
```

Автоматический pgBackRest-path работает fail-closed и разрешается только после подписанного успешного ручного PITR/DR drill. `rebuild-replica.sh` запрещает rebuild leader/primary и по умолчанию работает как dry-run.

Автоматическое удаление DCS, выбор PITR target и создание нового primary при полной потере кластера намеренно не автоматизированы.

### 5. Acceptance tooling

`deploy/acceptance/` содержит воспроизводимый acceptance cycle для реального стенда.

Preflight проверяет:

- `db1/db2/db3`, одного Patroni leader и replicas;
- lag replication;
- три etcd endpoints через TLS;
- pgBackRest health/backup freshness;
- MinIO DR replication;
- оба Alertmanager;
- `/health/` приложения.

Harness собирает UTC checkpoints и evidence, а при завершении рассчитывает observed RPO/RTO и формирует `RESULT.md`.

Он сознательно **не** выполняет destructive failure injection из CI: останов leader, promotion, DCS cleanup, PITR target selection и MinIO DNS/LB switch остаются операторскими change-controlled действиями.

## Структура репозитория

```text
apps/audit/           WORM-аудит
apps/core/            общие сервисы, storage, limits, domain events, /health/
apps/documents/       НРД, история, retention, permissions, OCR/tasks
apps/iam/             пользователи, auth/TOTP, импорт персонала
apps/search_ocr/      тезаурус, Smart Search, persisted search read-model
apps/templates_bank/  банк бланков
config/               Django/Celery/settings/urls
static/               CSS и дизайн-токены
templates/            Django templates
deploy/ha/            Patroni/etcd/PgBouncer/HAProxy
deploy/dr/            pgBackRest, PITR, rebuild и DR runbooks
deploy/minio/dr/      MinIO active-passive DR
deploy/monitoring/    exporters и monitoring helpers
deploy/prometheus/    Prometheus config/rules
deploy/grafana/       Grafana provisioning/dashboard
deploy/alertmanager/  Alertmanager HA/delivery
deploy/acceptance/    стендовый HA/DR acceptance harness
```

## Локальный запуск

Базовая связка разработки — Python 3.13 + Django 5.2 LTS. Подробная политика версий и целевая ОС находятся в [`STACK.md`](STACK.md).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

cp .env.example .env

docker compose up -d

# создать MinIO buckets для локальной разработки
bash deploy/minio/init-bucket.sh

.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```

Для OCR/фоновых задач в отдельном терминале:

```bash
.venv/bin/celery -A config worker -l info
```

`docker-compose.yml` предназначен для локальной разработки и интеграционных проверок. Он не моделирует физически разделённый production HA/DR-контур.

## Полезные management-команды

```bash
# повторный OCR
python manage.py rerun_ocr
python manage.py rerun_ocr --force

# полный rebuild поискового read-model
python manage.py rebuild_search_index

# синхронный импорт персонала
python manage.py import_personnel personnel.xlsx

# асинхронный импорт персонала
python manage.py import_personnel personnel.xlsx --async

# состояние async-импорта
python manage.py personnel_import_status <task-id>
```

## Тесты и CI

Локально:

```bash
.venv/bin/coverage run manage.py test
.venv/bin/coverage report
```

Основной workflow `.github/workflows/ci.yml` проверяет:

- Django `manage.py check`;
- отсутствие незакоммиченных миграций;
- применение миграций;
- полный test suite с coverage;
- OCR/ClamAV окружение;
- monitoring validation Этапа 4;
- MinIO DR validation;
- Alertmanager validation;
- Patroni rebuild/combined DR validation;
- acceptance harness validation.

Правило проекта: изменение Django models должно сопровождаться миграцией в том же PR.

## Документация

- [`STACK.md`](STACK.md) — версии, платформа развёртывания и подробные архитектурные решения;
- [`DESIGN.md`](DESIGN.md) — дизайн-система Web GUI;
- [`docs/STAGE4_STATUS.md`](docs/STAGE4_STATUS.md) — фактический статус HA/DR;
- [`deploy/dr/README.md`](deploy/dr/README.md) — PostgreSQL backup/PITR/DR;
- [`deploy/dr/REBUILD_AND_DRILL.md`](deploy/dr/REBUILD_AND_DRILL.md) — rebuild и combined drill;
- [`deploy/monitoring/README.md`](deploy/monitoring/README.md) — monitoring HA/DR;
- [`deploy/acceptance/README.md`](deploy/acceptance/README.md) — программа реальной стендовой приёмки.

## Что остаётся

Ключевые незакрытые работы на текущем `main`:

1. Развернуть реальный HA/DR стенд: 3×Patroni/PostgreSQL, 3×etcd, две физически разделённые MinIO-площадки и 2×Alertmanager.
2. Провести planned switchover, unplanned failover, PITR, MinIO failover/failback, synthetic alert delivery и application smoke test.
3. Повторить combined drill вторым оператором.
4. Зафиксировать фактические RPO/RTO, утвердить SLA и заменить bootstrap alert thresholds на SLA-derived значения.
5. Решить оставшиеся UX/операционные ограничения OCR и распределённого throttling там, где они нужны для production-нагрузки.

До выполнения реального acceptance Этап 4 корректно считать **функционально реализованным, но не эксплуатационно принятым**.
