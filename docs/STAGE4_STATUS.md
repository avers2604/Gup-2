# Этап 4 — фактический статус репозитория

Дата перепроверки: 2026-09-10.

Этот файл фиксирует состояние кода и эксплуатационных шаблонов. Наличие
конфигураций и acceptance tooling в Git не считается доказательством
пройденного HA/DR испытания и не подтверждает RPO/RTO без реального стенда.

## Партия 1 — HA PostgreSQL + DR bootstrap

После merge PR #29 в `main` присутствуют:

- `deploy/ha/` — 3-node Patroni/PostgreSQL baseline, 3-node etcd/TLS,
  локальные HAProxy + PgBouncer;
- `deploy/dr/` — pgBackRest, continuous WAL archive, PITR и full-cluster DR
  runbook;
- стабильная DB-точка приложения `127.0.0.1:6432` через
  `Django -> PgBouncer -> HAProxy -> Patroni primary`.

## Партия 2 — monitoring HA/DR

После merge PR #30 в `main` присутствуют Patroni/PostgreSQL/PgBouncer/
pgBackRest metrics, Prometheus rules, Grafana dashboard и CI validation.
Bootstrap alert thresholds не являются SLA.

## Партия 3 — MinIO DR + Alertmanager delivery

После merge PR #31 в `main` присутствуют:

- active-passive bucket replication для `bz-get-originals` и
  `bz-get-working`;
- Object Lock/WORM + Versioning для `originals`;
- least-privilege replication policies и failover/failback runbook;
- двухузловой Alertmanager HA с раздельными warning/critical routes,
  synthetic alert test и self-monitoring.

Operational alerting считается принятым только после проверки реальной
доставки на стенде.

## Партия 4 — guarded Patroni rebuild + combined DR drill

После merge PR #32 в `main` присутствуют:

- `create_replica_methods = pgbackrest -> basebackup`;
- fail-closed `patroni-pgbackrest-restore.sh` только для replica/standby;
- `rebuild-replica.sh` с dry-run, запретом leader rebuild и preflight;
- approval gate после подписанного ручного PITR/DR drill;
- `combined-drill.sh` для evidence, UTC checkpoints и наблюдаемых RPO/RTO;
- runtime-style CI проверки recovery path.

Replica rebuild не является full-cluster recovery: автоматическое удаление DCS,
выбор PITR target и создание нового primary при полной потере кластера остаются
отдельной change-controlled процедурой.

## Партия 5 — стендовый acceptance cycle

После merge PR #33 в `main` присутствует воспроизводимый acceptance harness для
реального HA/DR стенда.

`deploy/acceptance/acceptance-cycle.sh` запускается с operations/bastion host и
перед испытаниями fail-closed проверяет:

- точный состав Patroni `db1/db2/db3`, один leader и состояние replicas;
- replication lag относительно стендового лимита;
- здоровье всех трёх etcd endpoints через TLS;
- `pgbackrest check`, наличие и свежесть backup;
- MinIO active-passive replication через существующий DR helper;
- доступность обоих Alertmanager;
- health приложения через `/health/`.

`/health/` возвращает `200 healthy`, только если Django может выполнить
минимальный запрос к настроенной БД; при `DatabaseError` возвращается
`503 unhealthy` без раскрытия деталей инфраструктуры.

Harness не выполняет destructive failure injection, promotion, DCS cleanup,
PITR target selection или переключение MinIO DNS/LB. Эти действия остаются
явными operator steps из `deploy/dr/REBUILD_AND_DRILL.md`.

Во время испытания harness записывает UTC checkpoints. На финализации оператор
задаёт фактические времена incident/last durable/service restored и отдельные
результаты PostgreSQL, MinIO, alert delivery и application validation.
`RESULT.md` вычисляет observed RPO/RTO; любой FAIL делает общий результат FAIL и
возвращает ненулевой exit code.

`deploy/acceptance/validate.sh` проверяет acceptance orchestration в CI на fake
Patroni/etcd/pgBackRest/MinIO/Alertmanager, но не заменяет реальный стенд.

### Ревизия harness перед стендовой сессией

Стенд из репозитория не поднимается, но ошибка в самой оснастке обойдётся
дороже всего именно на стенде: испытание проводится по change-plan, окно
ограничено, а повтор стоит отдельного согласования. Поэтому перед выездом
harness разобран отдельно и в нём исправлены четыре дефекта.

1. **CI ослаблял ту самую проверку, которую валидировал.** `validate.sh`
   подставлял `APP_HEALTH_EXPECT_REGEX=healthy` без якорей. Проверено:
   `grep -Eiq "healthy" <<< "unhealthy"` **совпадает**, то есть CI подтвердил бы
   harness, принимающий нездоровое приложение, — при том что `/health/` отдаёт
   именно `healthy` / `unhealthy`. Поставляемая по умолчанию регулярка
   `^(ok|healthy|ready)$` якорная и корректна, но в CI не проверялась. Теперь CI
   использует ту же якорную регулярку, что и инвентарь, и добавлен
   отрицательный случай: подставной `/health/`, отдающий `unhealthy`, обязан
   валить preflight.
2. **`finalize` игнорировал настраиваемый критерий.** `acceptance.env.example`
   предлагает `SEARCH_REINDEX_DOCUMENTS` как настройку, `extended-checks.sh`
   честно передавал её в `--require-count`, а `finalize` сравнивал с зашитым
   `10000`. Проверено: согласованный прогон на 5000 документах за 120 секунд
   давал `overall FAIL`. Теперь требуемый объём записывается в
   `extended-results.env` рядом с измерением, и `finalize` сверяет прогон именно
   с ним.
3. **Размер выборки MinIO не настраивался вовсе.** `500` было зашито и в
   `extended-checks.sh`, и в `finalize`, хотя соседние `SEARCH_*` настраиваются.
   Если в bucket стенда меньше 500 объектов, приёмка не прошла бы никогда без
   правки скрипта. Введён `MINIO_HASH_SAMPLE_SIZE` (по умолчанию 500), требуемое
   число пишется в evidence и выводится в `RESULT.md`: снижение выборки стало
   видимым в отчёте решением, а не молчаливой правкой кода.
4. **`RESULT.md` винил не ту причину.** Единственная строка `DISCREPANCY`
   утверждала про лимит времени независимо от того, какое из условий не
   выполнилось: прогон на 5000 документах за 120 секунд при лимите 3600 получал
   вердикт «не уложился в 60 минут». Теперь на каждое невыполненное условие —
   объём, время, статус измерения, mismatches, операторские флаги — выводится
   своя строка с фактическими числами.

Первые два дефекта подтверждены воспроизведением до правки, все четыре закрыты
отрицательными случаями в `validate.sh`. Это не приближает Этап 4 к приёмке —
список ниже не сокращается ни на пункт, — но снимает риск потратить стендовое
окно на разбор ложного FAIL.

## Обязательные архитектурные ограничения

### Dev Compose не является HA/DR

Один PostgreSQL, один etcd и один MinIO container в `docker-compose.yml` —
только dev-среда. Несколько volume одного host не создают отдельный failure
domain.

### Репликация не является backup

PostgreSQL streaming replication и MinIO bucket replication защищают от
отказов, но не заменяют независимый backup/restore. Логическая ошибка может
распространиться на replicas.

### PostgreSQL и MinIO должны восстанавливаться согласованно

БД хранит metadata/object keys, файлы находятся в S3. Успешный PostgreSQL
restore при отсутствующей соответствующей object version не считается
успешным DR всей АИС.

### Monitoring и generated RESULT.md не равны SLA

SLA возникает только после утверждения Заказчиком RPO/RTO и подтверждения их
реальными измеренными drills.

## Что остаётся до закрытия Этапа 4

1. Подготовить реальный стенд: 3x etcd + 3x Patroni/PostgreSQL.
2. Развернуть две физически разделённые MinIO площадки.
3. Развернуть два Alertmanager и подключить реальные warning/critical receivers.
4. Заполнить `/etc/bz-get/stage4-acceptance.env` реальными endpoints/TLS paths,
   а также согласовать с Заказчиком объёмы приёмки (`SEARCH_REINDEX_DOCUMENTS`,
   `MINIO_HASH_SAMPLE_SIZE`): значения по умолчанию — 10 000 и 500.
5. Выполнить acceptance preflight и сохранить evidence.
6. Провести planned switchover и unplanned PostgreSQL failover.
7. Провести full/diff/incr pgBackRest + continuous WAL и isolated PITR.
8. Провести MinIO failover/failback/resync и проверить object versions,
   checksums и WORM retention.
9. Проверить synthetic alert delivery и отказ одного Alertmanager peer.
10. После PASS ручного PITR проверить pgBackRest-based rebuild одной Patroni
    replica.
11. Повторить combined acceptance другим оператором.
12. Утвердить измеренные RPO/RTO и заменить bootstrap alert thresholds на
    SLA-derived значения.

## Граница автоматизации

Репозиторий содержит инструменты для воспроизводимого acceptance cycle, но сам
реальный стенд из GitHub создать или признать принятым нельзя. Результат Этапа 4
становится эксплуатационно принятым только после запуска этих процедур на
целевой/приёмочной инфраструктуре и подписанного evidence.
