"""Шина доменных событий (apps/core/domain_events.py) и её подключение.

Синхронный ``publish()`` используется для критичных эффектов, которые должны
быть атомарны с исходной операцией: WORM-аудит, история паролей, сброс сессий.
Поэтому событие без подписчика — runtime-ошибка, а не тихий no-op.
``publish_after_commit()`` остаётся каналом для некритичных интеграций и может
не иметь подписчика.
"""
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from apps.core import domain_events


class DomainEventBusTests(TestCase):
    def setUp(self):
        self._snapshot = {name: list(handlers) for name, handlers in domain_events._HANDLERS.items()}

    def tearDown(self):
        domain_events._HANDLERS.clear()
        domain_events._HANDLERS.update(self._snapshot)

    def test_publish_dispatches_synchronously(self):
        received = []
        domain_events.register("test.sync")(lambda event: received.append(event))

        domain_events.publish("test.sync", value=42)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["value"], 42)

    def test_publish_propagates_handler_exception(self):
        def failing_handler(event):
            raise RuntimeError("обработчик упал")

        domain_events.register("test.failing")(failing_handler)
        with self.assertRaises(RuntimeError):
            domain_events.publish("test.failing")

    def test_register_is_idempotent(self):
        def handler(event):
            pass

        domain_events.register("test.idempotent")(handler)
        domain_events.register("test.idempotent")(handler)

        self.assertEqual(domain_events._HANDLERS["test.idempotent"].count(handler), 1)

    def test_publish_without_subscribers_fails_closed(self):
        with self.assertRaisesRegex(
            domain_events.MissingDomainEventHandler,
            "test.nobody.listens",
        ):
            domain_events.publish("test.nobody.listens", value=1)

    def test_unknown_payload_keys_reach_handler(self):
        received = []
        domain_events.register("test.payload")(lambda event: received.append(event.payload))

        domain_events.publish("test.payload", a=1, b="два")

        self.assertEqual(received[0], {"a": 1, "b": "два"})


class PublishAfterCommitTests(TransactionTestCase):
    """After-commit канал не является атомарным с исходной транзакцией."""

    def setUp(self):
        self._snapshot = {name: list(handlers) for name, handlers in domain_events._HANDLERS.items()}

    def tearDown(self):
        domain_events._HANDLERS.clear()
        domain_events._HANDLERS.update(self._snapshot)

    def test_fires_after_commit(self):
        received = []
        domain_events.register("test.after_commit")(lambda event: received.append(event))

        with transaction.atomic():
            domain_events.publish_after_commit("test.after_commit")
            self.assertEqual(received, [])

        self.assertEqual(len(received), 1)

    def test_handler_exception_is_swallowed_after_commit(self):
        def failing_handler(event):
            raise RuntimeError("некритичный потребитель упал")

        domain_events.register("test.after_commit_failing")(failing_handler)
        with transaction.atomic():
            domain_events.publish_after_commit("test.after_commit_failing")

    def test_without_subscribers_remains_noop_for_noncritical_channel(self):
        with transaction.atomic():
            domain_events.publish_after_commit("test.after_commit.no_subscribers", value=1)


class HandlerRegistrationTests(TestCase):
    def test_iam_handlers_are_registered(self):
        from apps.iam.handlers import logout_blocked_user, record_password_history

        self.assertIn(logout_blocked_user, domain_events._HANDLERS.get("user.blocked", []))
        self.assertIn(
            record_password_history, domain_events._HANDLERS.get("user.password.changed", [])
        )

    def test_audit_handlers_are_registered(self):
        from apps.audit.handlers import audit_user_role_changed, audit_user_role_elevated

        self.assertIn(audit_user_role_changed, domain_events._HANDLERS.get("user.role.changed", []))
        self.assertIn(
            audit_user_role_elevated, domain_events._HANDLERS.get("user.role.elevated", [])
        )

    def test_every_published_event_has_at_least_one_handler(self):
        for event_name in (
            "user.blocked",
            "user.role.changed",
            "user.role.elevated",
            "user.password.changed",
        ):
            with self.subTest(event=event_name):
                self.assertTrue(
                    domain_events._HANDLERS.get(event_name),
                    f"событие {event_name} публикуется, но обработчиков нет",
                )

    def test_user_save_is_not_monkey_patched(self):
        from apps.iam.models import User

        self.assertEqual(User.save.__qualname__, "User.save")
        self.assertEqual(User.save.__module__, "apps.iam.models")
