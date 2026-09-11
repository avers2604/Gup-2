"""Ретрай записи, отклонённой read-only узлом (страховка к Ф-4).

Проверяется не «функция вызвалась дважды», а поведение, от которого зависит
приёмка: повтор происходит ТОЛЬКО на SQLSTATE 25006, соединение перед повтором
закрывается (иначе повтор ушёл бы на тот же демоутнутый узел), и внутри уже
открытой вызывающим транзакции повтора не происходит.
"""
from unittest import mock

from django.db import DatabaseError, transaction
from django.test import TestCase

from apps.core.write_retry import (
    is_read_only_primary_error,
    retry_on_read_only_primary,
)


def _read_only_error():
    """DatabaseError в том виде, в каком его отдаёт Django поверх psycopg."""
    from psycopg import errors

    cause = errors.ReadOnlySqlTransaction("cannot execute INSERT in a read-only transaction")
    wrapped = DatabaseError("cannot execute INSERT in a read-only transaction")
    wrapped.__cause__ = cause
    return wrapped


class ReadOnlyErrorDetectionTests(TestCase):
    def test_detects_read_only_sqlstate_through_django_wrapper(self):
        self.assertTrue(is_read_only_primary_error(_read_only_error()))

    def test_other_database_errors_are_not_read_only(self):
        from psycopg import errors

        cause = errors.UniqueViolation("duplicate key")
        wrapped = DatabaseError("duplicate key")
        wrapped.__cause__ = cause
        self.assertFalse(is_read_only_primary_error(wrapped))
        self.assertFalse(is_read_only_primary_error(DatabaseError("no cause at all")))


class RetryOnReadOnlyPrimaryTests(TestCase):
    def test_retries_once_and_closes_connection_first(self):
        calls = []

        @retry_on_read_only_primary
        def write(value):
            calls.append(value)
            if len(calls) == 1:
                raise _read_only_error()
            return "ok"

        with mock.patch("apps.core.write_retry.connection") as conn:
            conn.in_atomic_block = False
            result = write("x")

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["x", "x"])
        # Без закрытия соединения повтор ушёл бы через тот же серверный
        # коннект PgBouncer — то есть снова на демоутнутый узел.
        conn.close.assert_called_once_with()

    def test_second_read_only_failure_is_raised_not_retried_forever(self):
        calls = []

        @retry_on_read_only_primary
        def always_read_only():
            calls.append(1)
            raise _read_only_error()

        with mock.patch("apps.core.write_retry.connection") as conn:
            conn.in_atomic_block = False
            with self.assertRaises(DatabaseError):
                always_read_only()

        self.assertEqual(len(calls), 2, "одна повторная попытка, не больше")

    def test_unrelated_database_error_is_not_retried(self):
        calls = []

        @retry_on_read_only_primary
        def failing():
            calls.append(1)
            raise DatabaseError("deadlock detected")

        with self.assertRaises(DatabaseError):
            failing()

        self.assertEqual(len(calls), 1)

    def test_inside_callers_transaction_the_error_is_propagated(self):
        """Повтор внутри чужой транзакции невозможен: она уже к откату."""
        calls = []

        @retry_on_read_only_primary
        def write():
            calls.append(1)
            raise _read_only_error()

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                write()

        self.assertEqual(len(calls), 1, "внутри atomic повтора быть не должно")

    def test_successful_call_is_not_wrapped_in_extra_attempts(self):
        calls = []

        @retry_on_read_only_primary
        def write():
            calls.append(1)
            return 42

        self.assertEqual(write(), 42)
        self.assertEqual(len(calls), 1)

    def test_decorator_preserves_function_identity(self):
        @retry_on_read_only_primary
        def documented():
            """docstring сохраняется"""

        self.assertEqual(documented.__name__, "documented")
        self.assertEqual(documented.__doc__, "docstring сохраняется")
