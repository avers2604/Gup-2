# Этап 4 — P2: IAM lockout projection и audit boundary

Статус: вторая партия P2, stacked поверх fail-closed domain event bus. Это application hardening; реальный HA/DR стенд для доказательства корректности самой проекции не требуется.

## Проблема

До этой партии `apps/iam/services.py` использовал WORM `AuditLog` сразу в двух ролях:

1. как доказательный неизменяемый журнал событий безопасности;
2. как оперативное состояние механизма блокировки входа.

Это создавало прямую зависимость IAM → audit и дорогой IP-запрос по `AuditLog.details` (`JSONField`) на каждом auth request. На большом WORM-журнале такой query path плохо соответствует назначению lockout-контроля.

## Новая модель

`LoginFailure` в `apps.iam.models` — компактная operational projection только для sliding-window lockout:

- `personnel_number`;
- `ip_address`;
- `stage` (`credentials` / `totp` и совместимые значения);
- `reason`;
- `created_at`.

Два B-tree индекса соответствуют фактическим security queries:

- `(personnel_number, created_at)`;
- `(ip_address, created_at)`.

`is_locked_out()`, `is_ip_locked_out()` и вычисление `Retry-After` читают только эту projection. JSON-поиск по WORM-журналу из auth hot path удалён.

## Атомарность с WORM-аудитом

Неудачная попытка входа проходит через `_record_login_failure()` под `transaction.atomic()`:

1. создаётся `LoginFailure`;
2. синхронно публикуется critical event `auth.login.failed`;
3. handler в `apps.audit` создаёт `SESSION_LOGIN_FAILED` в WORM-журнале.

P2 fail-closed bus гарантирует: если audit handler отсутствует или падает, транзакция откатывается и operational projection не расходится с доказательным журналом.

Успешные `SESSION_LOGIN`, `SESSION_LOGOUT` и `USER_TOTP_RESET` также переведены с прямого `AuditLog.objects.create()` на critical domain events:

- `auth.session.login`;
- `auth.session.logout`;
- `auth.totp.reset`.

`AuditConfig.ready()` требует все эти handlers при startup.

## Переход без окна разблокировки

Миграция `iam.0009_loginfailure` после создания projection копирует из WORM `AuditLog` только `session.login_failed`, которые находятся внутри активного 15-минутного lockout window на момент миграции.

Исходный `created_at` восстанавливается после вставки, поэтому переход не:

- снимает существующую блокировку;
- продлевает её заново на 15 минут;
- переносит многолетнюю историю аудита в operational table.

## Retention operational projection

`LoginFailure` не является WORM-журналом и не хранится бессрочно. Добавлена management-команда:

```bash
python manage.py purge_login_failures --older-than-hours 24
```

Baseline retention — 24 часа, то есть существенно больше 15-минутного lockout window и достаточно для оперативной диагностики. Команда удаляет только `LoginFailure`; неизменяемый `AuditLog` не меняется и остаётся источником аудиторских доказательств.

На production эту команду следует запускать периодически штатным scheduler/systemd timer. Даже если cleanup временно не сработал, correctness lockout не меняется: security queries всегда ограничены текущим временным окном и используют индексы.

## Контракты тестов

Регрессии проверяют:

- `apps/iam/services.py` больше не импортирует `apps.audit` и не обращается к `AuditLog.objects`;
- одна credential failure создаёт и `LoginFailure`, и WORM event с теми же security-реквизитами;
- отсутствие critical audit handler откатывает projection;
- account lockout работает только по projection даже без соответствующих AuditLog rows;
- IP lockout работает по отдельному индексированному столбцу;
- оба требуемых индекса присутствуют в модели;
- retention cleanup удаляет только устаревшие operational rows;
- старые Web/API/TOTP lockout и `Retry-After` тесты продолжают проверять пользовательскую семантику.
