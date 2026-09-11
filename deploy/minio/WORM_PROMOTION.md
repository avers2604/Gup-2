# WORM promotion: staging → commit → originals

## Состояния файла

Immutable-файлы (`NormativeDocument.files_original`, `Template.file_editable`,
`Template.file_sample`) проходят четыре состояния:

1. **staged** — байты записаны в `working/staging/worm/`, PostgreSQL-транзакция ещё не завершена;
2. **committed / promotion pending** — карточка и `StagedFilePromotion` уже зафиксированы в PostgreSQL, final key известен, но объекта в `originals` ещё нет;
3. **promoted** — server-side copy в `originals` завершён, Object Lock/Legal Hold и SHA-256 проверены, `completed_at` записан;
4. **staging cleanup** — исходная mutable-копия удаляется после успешного commit promotion. Ошибка cleanup не отменяет уже проверенный WORM-объект; lifecycle удаляет остаток позже.

Пользователь **не получает staging-файл**. Пока состояние `promotion pending`, Web UI показывает «закрепляется в защищённом хранилище», а прямой download endpoint возвращает `409 Conflict` с `Retry-After`. Счётчик скачиваний и метрика сбоев генерации ссылок при этом не увеличиваются.

## Снимок retention

`lock_mode`, `retain_until` и `legal_hold` вычисляются в момент DB commit исходной записи и сохраняются в `StagedFilePromotion`. Worker не перечитывает текущую категорию retention при копировании. Поэтому последующее изменение юридической категории является отдельным действием и не способно молча изменить параметры уже созданного намерения.

## Откат

До DB commit rollback удаляет только mutable staging. В `originals` ничего ещё не записано.

После успешного promotion физический rollback объекта **невозможен и не должен выполняться**. Object Lock существует именно для этого. Исправление ошибочной публикации выполняется доменным переходом статуса (`Действует → Черновик` или `Аннулирован`) с обязательным основанием и WORM-аудитом; immutable object/version остаётся доказательством произошедшей операции. Никакой код rollback не вызывает DELETE для `originals`.

## OCR и отсутствие взаимной блокировки

Зависимость односторонняя:

`OCR → ждёт завершения WORM promotion`

Promotion не ждёт OCR и не берёт блокировку OCR-состояния. Если сообщения outbox приходят разным workers одновременно, OCR видит незавершённый `StagedFilePromotion` и делает bounded Celery retry. После `completed_at` следующий запуск читает final key из `originals`. Поэтому цикла ожиданий нет.

## Наблюдаемость

`/metrics/business/` экспортирует:

- `bz_get_worm_promotions_pending`;
- `bz_get_worm_promotion_oldest_seconds`.

Prometheus rules:

- `WormPromotionStuck`: oldest > 900 секунд, `critical`;
- `WormPromotionBacklog`: pending > 0 непрерывно 15 минут, `warning`.

При алерте проверить `StagedFilePromotion.last_error`, TaskOutbox/Celery, доступность обеих MinIO-площадок, Object Lock bucket configuration и репликацию `working/staging/worm/`.

## DR

`working` реплицируется active → passive целиком, включая `staging/worm/`. Это важно: после DB commit durable promotion intent должен иметь доступ к исходным байтам даже при потере активной MinIO-площадки до завершения promote.
