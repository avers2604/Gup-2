# Этап 4 — P1 hardening acceptance evidence

Статус: кодовая оснастка P1. Реальный HA/DR стендовый прогон этим документом не подтверждается.

## Цель

P1 закрывает пять классов риска:

1. «cold reindex» должен действительно начинаться с пустого read-model и не должен запускаться случайно на обычной production-базе;
2. оператор не должен иметь возможности молча ослабить критерии через локальный `acceptance.env` или обход preflight;
3. MinIO DR должен проверяться на уровне конкретных object versions и WORM-параметров воспроизводимой выборкой;
4. итоговый PASS должен требовать полный набор обязательных стендовых checkpoints, а не только итоговые операторские флаги;
5. бизнес-метрики поиска/ссылок должны отражать логические события и выбранный период, а не HTTP pagination, повторы и lifetime accumulation.

## Repository-controlled baseline и waiver

Авторитетный baseline находится в `deploy/acceptance/acceptance-policy.json`:

- `search_reindex_documents_min = 10000`;
- `search_reindex_seconds_max = 3600`;
- `minio_version_sample_min = 500`.

Локальный inventory может делать критерии строже. Ослабление любого критерия без полного waiver приводит к FAIL. Проверка выполняется не только в `preflight`: `extended-checks.sh` повторно валидирует policy при каждом прямом запуске, а `finalize` ещё раз пересчитывает её по фактически записанным критериям. Поэтому обход preflight не является обходом policy.

Полный waiver состоит из:

- `ACCEPTANCE_WAIVER_ID` — ссылка/идентификатор фактического согласования;
- `ACCEPTANCE_WAIVER_APPROVER` — кто утвердил;
- `ACCEPTANCE_WAIVER_REASON` — основание и границы исключения.

Успешный прогон с ослабленным критерием получает только `PASS_WITH_WAIVER`. В `RESULT.md` явно фиксируются ID, approver, reason и список weakened criteria. Такой результат нельзя представить как обычный PASS.

## Настоящий cold rebuild и destructive guard

Размер корпуса проверяется по source-of-truth `NormativeDocument.objects.count()`, а не по текущему `DocumentSearchIndex`. CI отдельно создаёт неполный index и подтверждает, что acceptance всё равно видит полный источник.

`rebuild_search_index --cold`:

- требует `--require-count`;
- проверяет source corpus size до destructive operation;
- требует `ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX=YES`;
- требует точную подтверждающую фразу `--confirm-cold-rebuild TRUNCATE_DOCUMENT_SEARCH_INDEX`;
- только после этих guards выполняет `TRUNCATE` persisted `DocumentSearchIndex`;
- затем перестраивает все FTS vectors из source-of-truth документов;
- включает очистку read-model в измеряемое время;
- возвращает `mode=cold` в JSON evidence.

`acceptance.env.example` поставляется с `ACCEPTANCE_ALLOW_DESTRUCTIVE_REINDEX=NO`. Значение `YES` должно появляться только в согласованном acceptance window/change. Одного доступа к management command или знания `--require-count` недостаточно для destructive запуска.

Обычный `rebuild_search_index` без `--cold` остаётся production-safe online UPSERT и acceptance evidence не считается.

## Обязательные real-stand checkpoints

`finalize` fail-closed требует evidence следующих checkpoints:

- `preflight-complete`;
- `planned-switchover-complete`;
- `unplanned-failover-complete`;
- `pitr-validated`;
- `replica-rebuild-validated`;
- `minio-failover-validated`;
- `minio-failback-validated`;
- `alertmanager-peer-loss-validated`;
- `application-smoke-validated`;
- `write-path-switchover-measured`.

`acceptance_checkpoints.py` валидирует CSV, UTC timestamps и полный required set. Даже если все `ACCEPTANCE_*_RESULT=PASS` и extended measurements выглядят успешными, отсутствие любого обязательного checkpoint делает итог FAIL и выводит missing list в `RESULT.md`.

## MinIO versioned WORM verification

Совместимое действие `minio-hash` вызывает `verify_versioned_sample.py`.

Перед выборкой source и DR bucket обязаны подтвердить:

- `Versioning=Enabled`;
- `ObjectLockEnabled=Enabled`.

Если `MINIO_BUCKET_ORIGINALS` совпал с `MINIO_BUCKET_WORKING`, verifier останавливается: acceptance разрешён только для защищённого originals bucket.

Выборка воспроизводима: population сортируется по `(key, version_id)`, затем используется отдельный seeded RNG, алгоритм имеет идентификатор `sorted-random-v1`. Seed берётся из `MINIO_HASH_SAMPLE_SEED`, а если он не задан — из `RUN_ID`. Seed и algorithm пишутся и в `minio-versioned-sample.csv`, и в итоговый `RESULT.md`. Один и тот же seed на одном и том же наборе versions даёт одну и ту же выборку независимо от порядка ответа S3 API.

Для каждой выбранной source version проверяются:

- тот же `VersionId` на DR site;
- SHA-256 конкретной версии;
- Object Lock mode;
- retain-until;
- legal hold;
- фактическая WORM-защита source version.

Для документов постоянного хранения допустим legal hold `ON` без `RetainUntilDate`: это считается защищённой source version. DR-копия должна иметь то же состояние. Равные байты двух незащищённых версий — FAIL; отсутствующая version, несовпадение SHA-256, retention или legal hold — FAIL.

## Семантика business metrics

### Zero-result search

`bz_get_search_requests_total` и `bz_get_search_zero_results_total` считают **логические поиски**, а не каждый HTTP GET.

Logical identity строится из:

- authenticated user id;
- нормализованного запроса (`casefold`, схлопывание whitespace);
- category filter;
- service filter;
- surface (`web` или `api`).

Полученный payload хэшируется; для первой страницы используется `cache.add()` с коротким TTL (baseline 30 секунд, `SEARCH_METRIC_DEDUP_SECONDS`). Production cache — Redis, поэтому dedupe работает между app workers. Страницы 2..N не создают событие вообще, а немедленный reload/retry того же first-page logical search в окне TTL считается один раз. Другой пользователь, filter или surface дают другой logical key.

Если cache временно недоступен, поиск не ломается: событие считается, а сбой dedupe логируется. Это сознательный fail-open только для observability, не для бизнес-операции.

Prometheus endpoint отдаёт persisted lifetime counters. Grafana считает показатель за выбранный период через `increase(...[$__range])`, а не показывает lifetime ratio как значение текущего time range.

### Link-generation failures — ТЗ 2.2 §4.8

`bz_get_link_generation_failures_total{status="403|404|504"}` считает отдельно инфраструктурные ошибки, возникшие **после фактической попытки получить storage/link URL**:

- storage permission failure → `403`;
- storage/object-not-found при генерации ссылки → `404`;
- timeout/неизвестный storage failure → `504`.

Ожидаемые application states метрику не загрязняют:

- неизвестный route/field — business 404;
- невидимый/отсутствующий документ — business 404;
- отсутствующий file field — business 404;
- незавершённый WORM promotion — 409 + `Retry-After`.

Регрессионные тесты проверяют обе стороны: business 404/409 не увеличивают counters, а реальные storage 403/504 увеличивают соответствующую series; обработка storage HTTP 404 идёт тем же `_status_from_exception` контрактом. Grafana показывает прирост 403/404/504 за выбранный период через `increase(...[$__range])`.

## CI contracts

CI должен доказать как минимум:

- baseline → `PASS`;
- stricter criteria → `PASS` без waiver;
- weaker criteria без waiver → FAIL;
- прямой `extended-checks.sh` без preflight не обходит policy;
- weaker criteria с полным waiver → `PASS_WITH_WAIVER` с ID/approver/reason/weakened criteria в отчёте;
- destructive cold mode требует env opt-in и confirmation phrase;
- corpus size берётся из `NormativeDocument`, даже если index неполон;
- acceptance search measurement имеет `mode=cold`;
- обязательные real-stand checkpoints нужны для итогового PASS;
- MinIO verifier сравнивает VersionId/SHA/Object Lock, legal-hold-only protection, protected originals bucket и deterministic sample;
- фактическое невыполнение даже ослабленного критерия остаётся FAIL;
- pagination и короткие повторы не увеличивают logical search counters;
- business 404/409 не увеличивают storage link failure counters, инфраструктурные 403/404/504 остаются раздельными series;
- business dashboard использует period-scoped `increase()` для search/link counters.

## Что P1 не доказывает

Репозиторий не может сам подтвердить:

- новый write-path RTO после PgBouncer primary guard;
- реальный planned/unplanned Patroni switch на production-like topology;
- фактические 10 000 документов за <= 3600 секунд;
- реальные 500 MinIO object versions между физически разделёнными площадками;
- фактическое утверждение waiver Заказчиком;
- SLA/RPO/RTO.

Эти пункты закрываются только evidence реального стендового запуска и отдельным утверждением Заказчика/эксплуатации.
