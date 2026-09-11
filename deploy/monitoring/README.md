# Этап 4 — monitoring HA/DR и бизнес-индикаторы

Статус: **reference implementation / требуется стендовая проверка**.

Prometheus собирает как инфраструктурные, так и обязательные бизнес-метрики;
Grafana визуализирует их, Alertmanager доставляет warning/critical notifications.
Exporter/metrics endpoints должны быть доступны только из monitoring сети.

## Инфраструктурный контур

```text
DB nodes
  Patroni :8008 /metrics ---------+
  postgres_exporter :9187 --------+----> Prometheus ----> Grafana
                                   |          |
App nodes                          |          +----> Alertmanager 1/2
  PgBouncer exporter :9127 -------+
  Django /metrics/business/ ------+
                                   |
Backup/ops node                    |
  pgBackRest exporter :9854 ------+
```

### Patroni/PostgreSQL/PgBouncer/pgBackRest

Контролируются primary/sync standby, PostgreSQL availability, DCS freshness,
failsafe/pending restart, replication lag, PgBouncer waiting clients/max wait,
а также здоровье и свежесть pgBackRest backup/WAL archive.

Reference scrape/rules:

- `deploy/prometheus/prometheus.stage4.yml.example`;
- `deploy/prometheus/rules/ha-dr.yml`;
- `deploy/prometheus/rules/alert-delivery.yml`.

DB exporter работает под отдельной `pg_monitor` ролью через локальный Unix
socket; PgBouncer exporter — только через `stats_users`; pgBackRest exporter
read-only и выполняет только `pgbackrest info --output=json`.

## Обязательные бизнес-метрики ТЗ

Django экспортирует `GET /metrics/business/`. Счётчики поисков и ошибок ссылок
хранятся в PostgreSQL, а не process memory, поэтому не теряются между
Gunicorn workers/restarts. Prometheus скрапит оба app instance; поскольку оба
видят одну БД, запросы Grafana используют `max()`, а не `sum()`.

| Индикатор | Prometheus metric | Семантика |
|---|---|---|
| Просрочки очереди вычитки OCR >14 дней | `bz_get_ocr_review_overdue_total` | текущие документы `needs_review`, у которых стабильный `required_at` старше 14 дней |
| Бланки без ревизии >3 лет | `bz_get_templates_revision_overdue_total` | активные бланки с `last_reviewed_at` старше 3 лет; при NULL — опубликованные более 3 лет назад |
| Доля поисков с нулевой выдачей | `bz_get_search_zero_result_ratio` | `zero_results / valid_search_requests`; дополнительно экспортируются оба монотонных counter |
| Сбои генерации ссылок | `bz_get_link_generation_failures_total{status="403|404|504"}` | централизованный endpoint генерации/redirect URL фиксирует соответствующий класс отказа storage/link generation |

Dashboard: `BZ GET — Stage 4 Business Indicators`
(`deploy/grafana/provisioning/dashboards/json/stage4-business.json`). Он содержит
отдельную панель для каждого из четырёх индикаторов.

Метрика OCR не использует `NormativeDocument.updated_at` как возраст очереди:
несвязанное редактирование документа не должно обнулять 14-дневный таймер.
`OcrReviewQueueEntry.required_at` создаётся при переходе OCR в `needs_review` и
удаляется после успешной обработки. Для исторических записей migration
инициализирует best-available timestamp из существующей карточки.

## Reliability-метрики приложения

Тот же endpoint `/metrics/business/` содержит эксплуатационные метрики, которые
не являются четырьмя бизнес-индикаторами ТЗ, но используются для контроля
надёжности приложения и maintenance jobs.

Для retention IAM projection экспортируются:

- `bz_get_login_failure_purge_success_total` — число успешно завершённых
  `purge_login_failures`;
- `bz_get_login_failure_purge_last_success_unixtime` — timestamp последнего
  успешного purge; `0` означает, что успешный запуск ещё ни разу не подтверждён.

Prometheus rule `LoginFailurePurgeStale` в
`deploy/prometheus/rules/application.yml` выдаёт warning, если heartbeat старше
48 часов (`172800` секунд), отсутствует или ещё равен `0`. Условие должно
сохраняться 15 минут. Это контроль housekeeping/capacity drift, а не lockout
correctness: online security queries по-прежнему ограничены 15-минутным окном.
Runbook и действия оператора: `docs/STAGE4_P2_LOCKOUT_PROJECTION.md`.

## Prometheus и Grafana

Reference Prometheus конфигурация опрашивает Patroni, PostgreSQL/PgBouncer,
pgBackRest, оба Alertmanager и `/metrics/business/` приложения. Каждый alert
отправляется напрямую обоим Alertmanager peers — единый LB перед ними не
используется как отдельный SPOF.

Перед выкладкой:

```bash
promtool check config /etc/prometheus/prometheus.yml
promtool check rules /etc/prometheus/rules/ha-dr.yml
promtool check rules /etc/prometheus/rules/alert-delivery.yml
promtool check rules /etc/prometheus/rules/application.yml
```

Provisioning Grafana содержит datasource `Infrastructure Prometheus` и два
Stage 4 dashboard:

- `BZ GET — Stage 4 HA / DR` — инфраструктура;
- `BZ GET — Stage 4 Business Indicators` — четыре бизнес-индикатора ТЗ.

## Bootstrap thresholds

Инфраструктурные пороги пока **стендовые, не SLA**: primary count !=1,
отсутствие sync standby, replay lag >16 MiB, DCS age >60 сек, PgBouncer wait,
backup freshness и состояние Alertmanager. После реального acceptance и
утверждения RPO/RTO они должны быть заменены SLA-derived значениями.

Бизнес-индикаторы в этой партии экспортируются и визуализируются; автоматические
пороговые alert rules для них должны задаваться только там, где ТЗ/Заказчик
зафиксировал порог реакции. Для двух индикаторов порог уже является частью
самой метрики (`OCR >14 дней`, `revision >3 лет`).

Порог `LoginFailurePurgeStale=48h` относится не к бизнес-SLA, а к внутреннему
maintenance guard: daily timer имеет двукратный запас до warning и не должен
незаметно простаивать несколько суток.

## Alert delivery

`deploy/alertmanager/` содержит двухузловой HA-контур, отдельные
warning/critical routes, receiver URL через root-owned `url_file`,
`send_resolved=true`, inhibition и synthetic `test-alert.sh`.

Фактические receiver endpoints/получатели являются эксплуатационными секретами
и должны быть проверены реальным synthetic warning + critical + resolved.

## Приёмочный drill

Во время HA/DR acceptance требуется подтвердить:

1. failover Patroni и возврат к одному primary;
2. стабилизацию sync standby/replication lag;
3. PgBouncer без устойчивой очереди;
4. pgBackRest archive/backup после failover;
5. Alertmanager delivery при потере одного peer;
6. наличие всех четырёх `bz_get_*` бизнес-индикаторов в Prometheus;
7. отображение всех четырёх панелей business dashboard;
8. корректное изменение zero-result ratio после тестовых поисков;
9. инкремент 403/404/504 link counters при контролируемых тестовых отказах;
10. после ручного/таймерного `purge_login_failures` обновляется
   `bz_get_login_failure_purge_last_success_unixtime`, а synthetic stale-state
   переводит `LoginFailurePurgeStale` в firing и затем resolved.

Конфигурации в Git не являются доказательством operational acceptance: нужны
реальные scrape/delivery/drill evidence на стенде.
