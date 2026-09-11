"""Rate limiting / lockout на подбор пароля или TOTP-кода (ТЗ 4.7,
apps/iam/services.py). Пороги (5 попыток / 15 минут) — те же цифры, что
уже вшиты в Grafana-алерт «5+ неудачных попыток подряд»
(deploy/grafana/provisioning/alerting/audit-alerts.yml)."""
import datetime
from unittest.mock import patch

import pyotp
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog

from .. import services
from ..models import Department, User
from ..totp import generate_totp_secret


def _request(ip="203.0.113.5"):
    return RequestFactory().post("/", REMOTE_ADDR=ip)


def _make_user(totp_enabled=False, **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number="0001", last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=User.Role.READER,
        status=User.Status.ACTIVE,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass")
    if totp_enabled:
        user.totp_secret = generate_totp_secret()
        user.totp_enabled = True
    user.save()
    return user


class IsLockedOutTests(TestCase):
    def setUp(self):
        _make_user()

    def test_below_threshold_is_not_locked(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS - 1):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        self.assertFalse(services.is_locked_out("0001"))

    def test_at_threshold_is_locked(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        self.assertTrue(services.is_locked_out("0001"))

    def test_nonexistent_personnel_number_can_still_be_locked(self):
        # Счётчик копится независимо от того, существует ли учётка — иначе
        # сам факт блокировки утекал бы информацию о её наличии.
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="9999999", password="whatever")
        self.assertTrue(services.is_locked_out("9999999"))

    def test_attempts_outside_window_do_not_count(self):
        old_time = timezone.now() - datetime.timedelta(minutes=16)
        with patch("django.utils.timezone.now", return_value=old_time):
            for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
                services.check_credentials(None, personnel_number="0001", password="wrong")
        self.assertFalse(services.is_locked_out("0001"))

    def test_independent_per_personnel_number(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        self.assertFalse(services.is_locked_out("0002"))


class CheckCredentialsLockoutTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_sixth_attempt_is_blocked_even_with_correct_password(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        with self.assertRaises(services.LoginBlocked):
            services.check_credentials(None, personnel_number="0001", password="Sup3r$ecret!Pass")

    def test_blocked_attempt_does_not_write_new_audit_entry(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        count_before = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED).count()
        with self.assertRaises(services.LoginBlocked):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        count_after = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED).count()
        self.assertEqual(count_before, count_after)


class TotpLockoutTests(TestCase):
    def setUp(self):
        self.user = _make_user(totp_enabled=True)

    def test_totp_step_shares_same_lockout_counter(self):
        ticket = services.make_totp_pending_ticket(self.user)
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.verify_totp_login(ticket=ticket, code="000000")
        with self.assertRaises(services.LoginBlocked):
            services.verify_totp_login(ticket=ticket, code="000000")

    def test_correct_totp_code_blocked_after_threshold(self):
        ticket = services.make_totp_pending_ticket(self.user)
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.verify_totp_login(ticket=ticket, code="000000")
        correct_code = pyotp.TOTP(self.user.totp_secret).now()
        with self.assertRaises(services.LoginBlocked):
            services.verify_totp_login(ticket=ticket, code=correct_code)

    def test_password_step_failures_also_lock_totp_step(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number=self.user.personnel_number, password="wrong")
        ticket = services.make_totp_pending_ticket(self.user)
        with self.assertRaises(services.LoginBlocked):
            services.verify_totp_login(ticket=ticket, code="000000")


class WebLockoutIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        _make_user()

    def test_login_view_shows_lockout_message(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            self.client.post(reverse("iam:login"), {"personnel_number": "0001", "password": "wrong"})
        response = self.client.post(
            reverse("iam:login"), {"personnel_number": "0001", "password": "Sup3r$ecret!Pass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Слишком много неудачных попыток входа.")


class ApiLockoutIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _make_user()

    def test_token_obtain_returns_429_when_locked_out(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            self.client.post(reverse("iam_api:token-obtain"), {"personnel_number": "0001", "password": "wrong"})
        response = self.client.post(
            reverse("iam_api:token-obtain"), {"personnel_number": "0001", "password": "Sup3r$ecret!Pass"},
        )
        self.assertEqual(response.status_code, 429)


class IpLockoutTests(TestCase):
    """Второй, независимый контур — по IP (энумерация множества табельных
    номеров с одного источника, ТЗ 4.7 сам по себе такого порога не
    задаёт — см. docstring IP_LOCKOUT_MAX_ATTEMPTS)."""

    def test_many_different_personnel_numbers_same_ip_triggers_ip_lockout(self):
        request = _request()
        for i in range(services.IP_LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(request, personnel_number=f"999{i:04d}", password="wrong")
        self.assertTrue(services.is_ip_locked_out("203.0.113.5"))

    @override_settings(IAM_IP_LOCKOUT_MAX_ATTEMPTS=3)
    def test_ip_lockout_threshold_is_runtime_configurable(self):
        request = _request("192.0.2.44")
        for i in range(3):
            services.check_credentials(request, personnel_number=f"cfg{i:04d}", password="wrong")
        self.assertTrue(services.is_ip_locked_out("192.0.2.44"))

    @override_settings(IAM_IP_LOCKOUT_MAX_ATTEMPTS=0)
    def test_ip_lockout_threshold_rejects_zero(self):
        with self.assertRaises(ImproperlyConfigured):
            services.is_ip_locked_out("192.0.2.45")

    @override_settings(IAM_IP_LOCKOUT_MAX_ATTEMPTS="not-a-number")
    def test_ip_lockout_threshold_rejects_non_numeric_value(self):
        with self.assertRaises(ImproperlyConfigured):
            services.is_ip_locked_out("192.0.2.46")

    def test_ip_lockout_blocks_unrelated_account_from_same_source(self):
        user = _make_user()
        request = _request()
        for i in range(services.IP_LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(request, personnel_number=f"999{i:04d}", password="wrong")
        with self.assertRaises(services.LoginBlocked):
            services.check_credentials(request, personnel_number=user.personnel_number, password="Sup3r$ecret!Pass")

    def test_single_account_failures_do_not_trigger_ip_lockout(self):
        # LOCKOUT_MAX_ATTEMPTS (5) < IP_LOCKOUT_MAX_ATTEMPTS (20) — брутфорс
        # одной учётки блокирует её персонально, но не весь источник.
        request = _request()
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(request, personnel_number="0001", password="wrong")
        self.assertTrue(services.is_locked_out("0001"))
        self.assertFalse(services.is_ip_locked_out("203.0.113.5"))

    def test_different_ip_does_not_count_toward_ip_lockout(self):
        for i in range(services.IP_LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(_request(f"198.51.100.{i % 250}"), personnel_number=f"888{i:04d}", password="wrong")
        self.assertFalse(services.is_ip_locked_out("203.0.113.5"))

    def test_empty_ip_is_never_locked_out(self):
        for i in range(services.IP_LOCKOUT_MAX_ATTEMPTS * 2):
            services.check_credentials(None, personnel_number=f"777{i:04d}", password="wrong")
        self.assertFalse(services.is_ip_locked_out(""))


class RetryAfterTests(TestCase):
    def setUp(self):
        _make_user()

    def test_seconds_until_unlock_none_when_not_locked(self):
        self.assertIsNone(services.seconds_until_unlock("0001"))

    def test_seconds_until_unlock_positive_and_bounded_when_locked(self):
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(None, personnel_number="0001", password="wrong")
        seconds = services.seconds_until_unlock("0001")
        self.assertIsNotNone(seconds)
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, int(services.LOCKOUT_WINDOW.total_seconds()))

    def test_login_retry_after_seconds_combines_both_counters(self):
        request = _request()
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            services.check_credentials(request, personnel_number="0001", password="wrong")
        retry_after = services.login_retry_after_seconds("0001", "203.0.113.5")
        self.assertGreater(retry_after, 0)

    def test_login_retry_after_seconds_zero_when_not_locked(self):
        self.assertEqual(services.login_retry_after_seconds("0001", "203.0.113.5"), 0)

    def test_api_429_response_includes_retry_after_header(self):
        client = APIClient()
        for _ in range(services.LOCKOUT_MAX_ATTEMPTS):
            client.post(reverse("iam_api:token-obtain"), {"personnel_number": "0001", "password": "wrong"})
        response = client.post(
            reverse("iam_api:token-obtain"), {"personnel_number": "0001", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)
        self.assertGreater(int(response.headers["Retry-After"]), 0)


class LockoutQueryIndexTests(TestCase):
    def test_composite_index_on_event_type_personnel_number_created_at(self):
        index_field_sets = [tuple(idx.fields) for idx in AuditLog._meta.indexes]
        self.assertIn(
            ("event_type", "actor_personnel_number", "created_at"), index_field_sets,
        )
