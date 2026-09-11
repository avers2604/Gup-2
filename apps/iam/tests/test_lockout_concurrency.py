"""Регрессии конкурентного обхода account/IP lockout."""

import threading
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import RequestFactory, TransactionTestCase, override_settings

from apps.iam import services
from apps.iam.models import LoginFailure
from apps.iam.tests.test_auth_web import _make_user


class ConcurrentAccountLockoutTests(TransactionTestCase):
    def setUp(self):
        self.user = _make_user(personnel_number="0800")

    def test_only_one_parallel_attempt_can_consume_last_account_slot(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS - 1):
            LoginFailure.objects.create(
                personnel_number=self.user.personnel_number,
                ip_address="",
                stage="credentials",
                reason="seed",
            )

        both_authenticated = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def synchronized_authenticate(*args, **kwargs):
            try:
                # На старом коде оба потока проходят lockout pre-check при
                # четырёх неудачах и встречаются здесь до записи пятой.
                # После сериализации первый поток продолжит по timeout,
                # запишет пятую неудачу, а второй будет остановлен раньше.
                both_authenticated.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return None

        def worker():
            close_old_connections()
            try:
                result = services.check_credentials(
                    None,
                    personnel_number=self.user.personnel_number,
                    password="wrong",
                )
            except services.LoginBlocked:
                outcome = "blocked"
            else:
                outcome = "failed" if result is None else "authenticated"
            finally:
                connection.close()
            with outcome_lock:
                outcomes.append(outcome)

        # AuditLog — WORM и намеренно не очищается TRUNCATE. Здесь проверяем
        # именно операционную проекцию LoginFailure и окно гонки, поэтому
        # доменный publish подменяем no-op: продюсер аудита покрыт отдельно.
        with (
            patch("apps.iam.services.authenticate", side_effect=synchronized_authenticate),
            patch("apps.iam.services.publish"),
        ):
            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["blocked", "failed"])
        self.assertEqual(
            LoginFailure.objects.filter(personnel_number=self.user.personnel_number).count(),
            services.LOCKOUT_MAX_ATTEMPTS,
        )


class ConcurrentIpLockoutTests(TransactionTestCase):
    @override_settings(IAM_IP_LOCKOUT_MAX_ATTEMPTS=2)
    def test_only_one_parallel_attempt_can_consume_last_ip_slot(self):
        ip_address = "203.0.113.77"
        LoginFailure.objects.create(
            personnel_number="seed",
            ip_address=ip_address,
            stage="credentials",
            reason="seed",
        )

        both_authenticated = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def synchronized_authenticate(*args, **kwargs):
            try:
                both_authenticated.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return None

        def worker(personnel_number):
            close_old_connections()
            request = RequestFactory().post("/", REMOTE_ADDR=ip_address)
            try:
                result = services.check_credentials(
                    request,
                    personnel_number=personnel_number,
                    password="wrong",
                )
            except services.LoginBlocked:
                outcome = "blocked"
            else:
                outcome = "failed" if result is None else "authenticated"
            finally:
                connection.close()
            with outcome_lock:
                outcomes.append(outcome)

        with (
            patch("apps.iam.services.authenticate", side_effect=synchronized_authenticate),
            patch("apps.iam.services.publish"),
        ):
            threads = [
                threading.Thread(target=worker, args=("ip-race-1",)),
                threading.Thread(target=worker, args=("ip-race-2",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), ["blocked", "failed"])
        self.assertEqual(
            LoginFailure.objects.filter(ip_address=ip_address).count(),
            2,
        )
