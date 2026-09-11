"""Повтор записи, отклонённой демоутнутым узлом PostgreSQL.

Это последний рубеж защиты от находки Ф-4 (docs/STAGE4_LAB_REHEARSAL.md), а не
основной механизм. Основной — `deploy/ha/pgbouncer_primary_guard.py`: он
замечает смену лидера по Runtime API HAProxy и сбрасывает пул PgBouncer
командами RECONNECT/WAIT_CLOSE. Но guard оставляет окно: он опрашивает HAProxy
с интервалом (по умолчанию раз в секунду), а сам HAProxy ещё должен успеть
сойтись по health-check. Запрос, попавший в это окно, уйдёт на уже
демоутнутый узел, получит там живую сессию — и упадёт на первой же записи с
SQLSTATE 25006 (`cannot execute ... in a read-only transaction`).

Поэтому здесь: поймать именно эту ошибку, закрыть соединение (следующее
обращение возьмёт из пула другое, уже к новому лидеру) и повторить операцию
один раз.

ГРАНИЦЫ, которые важно понимать:

- Повторяется операция ЦЕЛИКОМ, а не часть транзакции. Декоратор применим
  только к функции, которая сама открывает свою транзакцию и при ошибке
  откатывается полностью, — то есть к сервисному слою, а не к произвольному
  куску кода посреди чужой транзакции.
- Если вызывающий уже открыл транзакцию (`connection.in_atomic_block`), повтор
  не выполняется: эта транзакция уже помечена к откату, и переигрывать внутри
  неё нечего. Ошибка пробрасывается вызывающему.
- Ретрай не подменяет собой guard и не делает переключение бесшовным: он лишь
  превращает одну отказавшую запись в задержку на время переоткрытия
  соединения.
"""
from __future__ import annotations

import functools
import logging

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)

# read_only_sql_transaction — PostgreSQL отвечает этим кодом на попытку записи
# на реплике/демоутнутом узле. Сверяемся именно с кодом, а не с текстом
# сообщения: текст локализуется и меняется между версиями.
READ_ONLY_SQLSTATE = "25006"


def is_read_only_primary_error(exc: BaseException) -> bool:
    """True, если ошибка — отказ записи на узле, ставшем read-only.

    Django оборачивает исключение драйвера в собственный DatabaseError, а
    оригинал остаётся в `__cause__`, поэтому проверяется вся цепочка. Имя
    атрибута с кодом различается: psycopg3 — `sqlstate`, psycopg2 — `pgcode`.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == READ_ONLY_SQLSTATE:
            return True
        current = current.__cause__
    return False


def retry_on_read_only_primary(func=None, *, attempts: int = 2):
    """Повторить вызов, если запись отклонена read-only узлом.

    attempts=2 означает одну повторную попытку. Больше одной бессмысленно:
    если после переоткрытия соединения узел снова read-only, значит проблема
    не в устаревшем соединении, а в самом кластере, и её должен видеть
    оператор через алерт PatroniPrimaryCountInvalid, а не маскировать ретрай.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except DatabaseError as exc:
                    if attempt == attempts or not is_read_only_primary_error(exc):
                        raise
                    if connection.in_atomic_block:
                        # Транзакция открыта вызывающим и уже помечена к
                        # откату — повторять её часть нельзя.
                        raise
                    logger.warning(
                        "%s: запись отклонена read-only узлом (попытка %d из %d); "
                        "переоткрываю соединение и повторяю",
                        fn.__qualname__, attempt, attempts,
                    )
                    connection.close()
            raise AssertionError("unreachable")  # pragma: no cover

        return wrapper

    if func is not None:
        return decorate(func)
    return decorate
