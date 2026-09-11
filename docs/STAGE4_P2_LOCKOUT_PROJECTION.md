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

## Источник истины при расхождении projection и WORM

Назначение двух хранилищ намеренно различается:

- **`LoginFailure` — операционный источник истины для решения о текущей блокировке.** Именно projection читают `is_locked_out()`, `is_ip_locked_out()` и `Retry-After` на каждом auth request.
- **`AuditLog` — полный неизменяемый источник аудиторских доказательств и расследований.** Он отвечает на вопрос «что фактически было зафиксировано системой», но не участвует в online lockout query path.

В штатном коде одна неудача записывает обе стороны атомарно, поэтому расхождение означает ручное вмешательство, повреждение данных или обход штатного write path. В таком случае текущая аутентификация продолжает принимать решение по `LoginFailure`; автоматически «достраивать» или изменять WORM-журнал запрещено.

Оператор должен рассматривать расхождение как incident/data-integrity anomaly: сохранить evidence, определить источник вмешательства и только затем восстанавливать mutable projection контролируемой процедурой на основании подтверждённых данных. `AuditLog` при reconciliation не редактируется и не удаляется.

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

## Порог IP lockout и корпоративный NAT

Account lockout остаётся нормативным baseline: **5 неудач за 15 минут для одного табельного номера**.

IP lockout — отдельный защитный контур против перебора множества учётных записей с одного источника. Его исходный baseline — **20 неудач с одного source IP за 15 минут**. Число `20` не взято из ТЗ и **не считается окончательно согласованным эксплуатационным нормативом**.

Порог задаётся через `IAM_IP_LOCKOUT_MAX_ATTEMPTS` (default `20`). Окно в этой партии остаётся 15 минут. Это позволяет менять threshold между стендом/площадками без изменения кода. Значение должно быть целым числом `>= 1`; `0`, отрицательное или нечисловое значение приводит к `ImproperlyConfigured`, а не к молчаливому отключению/искажению защиты. `IamConfig.ready()` проверяет effective threshold при запуске приложения, поэтому ошибочная security-конфигурация останавливает startup до обслуживания auth traffic. Этот контракт покрыт regression tests как на runtime validation, так и на вызов startup guard.

При приёмке необходимо проверить реальный fan-in корпоративной сети: NAT-шлюзы, reverse proxy, общие терминалы и рабочие места в депо могут объединять много сотрудников под одним source IP. Слишком низкий threshold даст false-positive блокировки всего источника. Поэтому перед утверждением production-значения следует минимум:

1. убедиться, что приложение получает корректный client IP с доверенного edge, а не адрес общего reverse proxy;
2. измерить обычный и пиковый объём auth failures на одном source IP;
3. провести тест с несколькими независимыми пользователями за корпоративным NAT;
4. выбрать threshold с запасом над легитимным fan-in и зафиксировать утверждённое значение в production environment/configuration record.

До такого стендового согласования `20/15 min` — безопасный технический default для разработки/acceptance, но не утверждённый SLA/security-policy параметр.

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

Для production в репозитории есть:

- `deploy/systemd/get-login-failure-purge.service`;
- `deploy/systemd/get-login-failure-purge.timer`.

Timer запускает cleanup ежедневно, использует `Persistent=true` и небольшой randomized delay. Его следует **включать только на одном scheduler/maintenance узле площадки**, аналогично single-instance scheduler: параллельный запуск на каждом app-node не нужен, хотя сама операция удаления устаревших rows идемпотентна.

Даже если cleanup временно не сработал, correctness lockout не меняется: security queries всегда ограничены текущим временным окном и используют индексы.

## Контракты тестов

Регрессии проверяют:

- `apps/iam/services.py` больше не импортирует `apps.audit` и не обращается к `AuditLog.objects`;
- одна credential failure создаёт и `LoginFailure`, и WORM event с теми же security-реквизитами;
- отсутствие critical audit handler откатывает projection;
- account lockout работает только по projection даже без соответствующих AuditLog rows;
- IP lockout работает по отдельному индексированному столбцу;
- IP threshold можно переопределить через Django setting/environment без изменения кода;
- invalid IP threshold (`0`/нечисловое значение) fail-closed отклоняется;
- startup guard повторно валидирует effective IP threshold;
- оба требуемых индекса присутствуют в модели;
- retention cleanup удаляет только устаревшие operational rows;
- старые Web/API/TOTP lockout и `Retry-After` тесты продолжают проверять пользовательскую семантику.
