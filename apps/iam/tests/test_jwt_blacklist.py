"""JWT blacklist (по запросу ревью анти-фрода) — apps/iam/api.LogoutView,
BLACKLIST_AFTER_ROTATION в SIMPLE_JWT (config/settings/base.py),
apps/iam/tasks.cleanup_expired_tokens."""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog

from ..models import Department, User
from ..tasks import cleanup_expired_tokens


def _make_user(**kwargs):
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
    user.save()
    return user


class LogoutEndpointTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        response = self.client.post(
            reverse("iam_api:token-obtain"),
            {"personnel_number": "0001", "password": "Sup3r$ecret!Pass"},
        )
        self.access = response.data["access"]
        self.refresh = response.data["refresh"]

    def test_logout_requires_authentication(self):
        client = APIClient()
        response = client.post(reverse("iam_api:logout"), {"refresh": self.refresh})
        self.assertEqual(response.status_code, 401)

    def test_logout_blacklists_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.post(reverse("iam_api:logout"), {"refresh": self.refresh})
        self.assertEqual(response.status_code, 205)

        with self.assertRaises(TokenError):
            RefreshToken(self.refresh).verify()

    def test_blacklisted_refresh_cannot_be_used_to_obtain_new_access(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.client.post(reverse("iam_api:logout"), {"refresh": self.refresh})

        response = self.client.post(reverse("iam_api:token-refresh"), {"refresh": self.refresh})
        self.assertEqual(response.status_code, 401)

    def test_logout_writes_session_logout_audit_entry(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        count_before = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGOUT).count()
        self.client.post(reverse("iam_api:logout"), {"refresh": self.refresh})
        count_after = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGOUT).count()
        self.assertEqual(count_after, count_before + 1)

    def test_logout_with_already_blacklisted_refresh_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        self.client.post(reverse("iam_api:logout"), {"refresh": self.refresh})
        response = self.client.post(reverse("iam_api:logout"), {"refresh": self.refresh})
        self.assertEqual(response.status_code, 400)

    def test_logout_with_garbage_refresh_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        response = self.client.post(reverse("iam_api:logout"), {"refresh": "not-a-real-token"})
        self.assertEqual(response.status_code, 400)


class RotateRefreshBlacklistTests(TestCase):
    """BLACKLIST_AFTER_ROTATION: обновление access через token/refresh/
    само по себе блэклистит ПРЕДЫДУЩИЙ refresh (не только явный logout)."""

    def setUp(self):
        self.user = _make_user()
        self.client = APIClient()
        response = self.client.post(
            reverse("iam_api:token-obtain"),
            {"personnel_number": "0001", "password": "Sup3r$ecret!Pass"},
        )
        self.original_refresh = response.data["refresh"]

    def test_using_refresh_blacklists_the_old_one(self):
        first_use = self.client.post(reverse("iam_api:token-refresh"), {"refresh": self.original_refresh})
        self.assertEqual(first_use.status_code, 200)

        second_use = self.client.post(reverse("iam_api:token-refresh"), {"refresh": self.original_refresh})
        self.assertEqual(second_use.status_code, 401)


class CleanupExpiredTokensTaskTests(TestCase):
    def test_task_invokes_flushexpiredtokens_management_command(self):
        with patch("apps.iam.tasks.call_command") as mocked:
            cleanup_expired_tokens()
        mocked.assert_called_once_with("flushexpiredtokens")

    def test_task_runs_against_real_blacklist_tables_without_error(self):
        user = _make_user()
        token = RefreshToken.for_user(user)
        token.blacklist()
        self.assertTrue(OutstandingToken.objects.exists())
        self.assertTrue(BlacklistedToken.objects.exists())

        cleanup_expired_tokens()
