from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import BusinessMetricCounter

from ..models import LoginFailure


class PurgeLoginFailuresTests(TestCase):
    def test_removes_only_rows_older_than_retention(self):
        now = timezone.now()
        with patch("django.utils.timezone.now", return_value=now - timedelta(hours=30)):
            old = LoginFailure.objects.create(
                personnel_number="old",
                ip_address="192.0.2.1",
                stage="credentials",
                reason="wrong_credentials",
            )
        recent = LoginFailure.objects.create(
            personnel_number="recent",
            ip_address="192.0.2.2",
            stage="credentials",
            reason="wrong_credentials",
        )

        out = StringIO()
        before = timezone.now()
        call_command("purge_login_failures", older_than_hours=24, stdout=out)
        after = timezone.now()

        self.assertFalse(LoginFailure.objects.filter(pk=old.pk).exists())
        self.assertTrue(LoginFailure.objects.filter(pk=recent.pk).exists())
        self.assertIn("Удалено operational LoginFailure: 1", out.getvalue())

        heartbeat = BusinessMetricCounter.objects.get(name="login_failure_purge_successes")
        self.assertEqual(heartbeat.value, 1)
        self.assertGreaterEqual(heartbeat.updated_at, before)
        self.assertLessEqual(heartbeat.updated_at, after)

    def test_each_successful_run_advances_purge_counter(self):
        call_command("purge_login_failures", older_than_hours=24, stdout=StringIO())
        call_command("purge_login_failures", older_than_hours=24, stdout=StringIO())

        heartbeat = BusinessMetricCounter.objects.get(name="login_failure_purge_successes")
        self.assertEqual(heartbeat.value, 2)

    def test_rejects_nonpositive_retention_without_marking_success(self):
        with self.assertRaisesMessage(CommandError, "--older-than-hours должен быть >= 1"):
            call_command("purge_login_failures", older_than_hours=0)

        self.assertFalse(
            BusinessMetricCounter.objects.filter(name="login_failure_purge_successes").exists()
        )
