# Этап 4 — P1 hardening acceptance evidence

Статус: кодовая оснастка P1. Реальный HA/DR стендовый прогон этим документом не подтверждается.

## Цель

P1 закрывает три класса риска в приёмочной процедуре:

1. «cold reindex» должен действительно начинаться с пустого read-model;
2. оператор не должен иметь возможности молча ослабить критерии через локальный `acceptance.env`;
3. MinIO DR должен проверяться на уровне конкретных object versions и WORM-параметров, а не только текущих байтов объекта.

## Repository-controlled baseline

Авторитетный baseline находится в `deploy/acceptance/acceptance-policy.json`:

- `search_reindex_documents_min = 10000`;
- `search_reindex_seconds_max = 3600`;
- `minio_version_sample_min = 500`.

Локальный inventory может делать критерии строже. Ослабление любого критерия без полного waiver приводит к fail-closed preflight.

Полный waiver состоит из:

- `ACCEPTANCE_WAIVER_ID` — ссылка/идентификатор фактического согласования;
- `ACCEPTANCE_WAIVER_APPROVER` — кто утвердил;
- `ACCEPTANCE_WAIVER_REASON` — основание и границы исключения.

Успешный прогон с ослабленным критерием получает `PASS_WITH_WAIVER`. Обычный `PASS` для такого прогона запрещён самим harness.

## Настоящий cold rebuild

`rebuild_search_index --cold`:

- требует `--require-count`;
- проверяет corpus size до destructive operation;
- выполняет `TRUNCATE` persisted `DocumentSearchIndex`;
- затем перестраивает все FTS vectors из source-of-truth документов;
- включает очистку read-model в измеряемое время;
- возвращает `mode=cold` в JSON evidence.

`extended-checks.sh ... cold-reindex` всегда передаёт `--cold`, а `finalize` дополнительно отказывает, если evidence не содержит `search_reindex_mode=cold`.

Обычный online UPSERT rebuild остаётся эксплуатационной операцией, но acceptance evidence не считается.

## MinIO versioned WORM verification

Совместимое действие `minio-hash` теперь вызывает `verify_versioned_sample.py`.

Для детерминированной случайной выборки source object versions проверяются:

- конкретный `VersionId` на DR site;
- SHA-256 этой версии;
- Object Lock mode;
- retain-until;
- legal hold;
- наличие фактической WORM-защиты source version.

Evidence: `minio-versioned-sample.csv`.

Равные байты двух незащищённых версий — FAIL. Отсутствующая версия, несовпадение SHA-256, retention или legal hold — FAIL.

## CI contracts

CI должен доказать как минимум:

- baseline -> `PASS`;
- stricter criteria -> `PASS` без waiver;
- weaker criteria без waiver -> FAIL до стендовых действий;
- weaker criteria с полным waiver -> `PASS_WITH_WAIVER`;
- неполный waiver -> FAIL;
- acceptance search measurement имеет `mode=cold`;
- MinIO verifier сравнивает VersionId/SHA/Object Lock и имеет отрицательные тесты;
- фактическое невыполнение даже ослабленного критерия остаётся FAIL.

## Что P1 не доказывает

Репозиторий не может сам подтвердить:

- новый write-path RTO после PgBouncer primary guard;
- реальный planned/unplanned Patroni switch на production-like topology;
- фактические 10 000 документов за <= 3600 секунд;
- реальные 500 MinIO object versions между физически разделёнными площадками;
- утверждение waiver Заказчиком;
- SLA/RPO/RTO.

Эти пункты закрываются только evidence реального стендового запуска и отдельным утверждением Заказчика/эксплуатации.
