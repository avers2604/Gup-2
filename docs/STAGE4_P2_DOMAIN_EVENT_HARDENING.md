# Этап 4 — P2: fail-closed domain events

Статус: первая партия P2, stacked на P1. Реальный стенд не требуется: это hardening транзакционных доменных границ приложения.

## Причина

`STACK.md` ранее фиксировал честную границу: если импорт обработчиков из `AppConfig.ready()` случайно пропадёт, синхронное критическое событие продолжит публиковаться «в пустоту». Операция в write-model завершится успешно, а WORM-аудит, история паролей или сброс сессий могут молча не выполниться.

Тесты регистрации ловили это только в CI. Runtime оставался fail-open.

## Новый контракт

`apps.core.domain_events.publish()` теперь предназначен только для **критичных синхронных** событий и работает fail-closed:

- если зарегистрирован хотя бы один handler — dispatch остаётся синхронным;
- исключение handler по-прежнему пробрасывается и откатывает текущую транзакцию;
- если handlers нет вообще — поднимается `MissingDomainEventHandler`;
- критичный side effect больше не может исчезнуть бесследно из-за сломанного импорта handler-модуля.

`publish_after_commit()` остаётся отдельным каналом для некритичных интеграций:

- выполняется после коммита;
- отсутствие subscribers допустимо;
- исключение handler логируется и не может откатить уже состоявшийся commit.

Таким образом, семантика двух API теперь различается не только документацией, но и runtime-поведением.

## Покрытие

`apps/core/tests/test_domain_events.py` проверяет:

- synchronous dispatch;
- propagation исключений;
- idempotent registration;
- обязательный FAIL при synchronous publish без handler;
- допустимый no-op без subscriber у after-commit channel;
- наличие известных IAM/audit handlers после `AppConfig.ready()`.

## Следующая партия P2

После этого guard безопасно переводить оставшиеся IAM audit writes на domain events. Отдельный оставшийся долг — lockout сейчас использует WORM `AuditLog` как оперативное security-state и IP-фильтр по JSON. Следующая партия должна вынести короткоживущую lockout-проекцию в IAM с индексами `(personnel_number, created_at)` и `(ip_address, created_at)`, оставив WORM-журнал доказательным контуром, а не рабочей таблицей security control.
