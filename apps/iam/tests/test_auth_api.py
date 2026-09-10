"""External API (apps/iam/api.py) — JWT-контур для интеграций."""
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from apps.audit.models import AuditLog

from ..models import Department, User
from ..totp import generate_totp_secret, totp_provisioning_uri, verify_totp_code


def _make_user(role=User.Role.READER, status=User.Status.ACTIVE, **kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number="0001", last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=role, status=status,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass")
    user.save()
    return user


class TotpServiceTests(APITestCase):
    """apps/iam/totp.py — чистые функции над pyotp, без БД."""

    def test_generated_secret_produces_verifiable_code(self):
        import pyotp

        secret = generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        self.assertTrue(verify_totp_code(secret=secret, code=code))

    def test_wrong_code_is_rejected(self):
        secret = generate_totp_secret()
        self.assertFalse(verify_totp_code(secret=secret, code="000000"))

    def test_empty_secret_or_code_rejected_without_crashing(self):
        self.assertFalse(verify_totp_code(secret="", code="123456"))
        self.assertFalse(verify_totp_code(secret=generate_totp_secret(), code=""))

    def test_provisioning_uri_contains_issuer_and_personnel_number(self):
        uri = totp_provisioning_uri(secret=generate_totp_secret(), personnel_number="0012478")
        self.assertIn("0012478", uri)
        self.assertIn("otpauth://totp/", uri)


class TokenObtainWithoutTotpTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()

    def test_correct_credentials_return_token_pair(self):
        response = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["totp_required"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_access_token_grants_access_to_protected_endpoint(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {obtain.data['access']}")
        response = client.get(reverse("iam_api:me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["personnel_number"], "0001")

    def test_me_without_token_is_rejected(self):
        response = APIClient().get(reverse("iam_api:me"))
        self.assertEqual(response.status_code, 401)

    def test_token_obtain_writes_session_login_audit_entry(self):
        self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entries.count(), 1)

    def test_wrong_password_rejected_with_generic_message(self):
        response = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access", response.data)

    def test_blocked_user_cannot_obtain_token(self):
        _make_user(personnel_number="0002", status=User.Status.BLOCKED)
        response = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0002", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 401)

    def test_refresh_token_issues_new_access_token(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam_api:token-refresh"), {"refresh": obtain.data["refresh"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)


class TokenObtainWithTotpTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret = generate_totp_secret()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def _current_code(self):
        import pyotp

        return pyotp.TOTP(self.secret).now()

    def test_login_with_totp_enabled_returns_ticket_not_tokens(self):
        response = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["totp_required"])
        self.assertIn("ticket", response.data)
        self.assertNotIn("access", response.data)

    def test_correct_totp_code_completes_login_with_tokens(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": obtain.data["ticket"], "code": self._current_code(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entries.count(), 1)

    def test_wrong_totp_code_does_not_issue_tokens(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": obtain.data["ticket"], "code": "000000",
        })
        self.assertEqual(response.status_code, 401)

    def test_garbage_ticket_rejected(self):
        response = self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": "not-a-real-ticket", "code": self._current_code(),
        })
        self.assertEqual(response.status_code, 401)

    def test_ticket_from_different_user_cannot_be_reused_after_block(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.user.status = User.Status.BLOCKED
        self.user.save()

        response = self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": obtain.data["ticket"], "code": self._current_code(),
        })
        self.assertEqual(response.status_code, 401)


class FailedLoginAuditApiTests(APITestCase):
    """Усиление аудита (решение Заказчика): SESSION_LOGIN_FAILED пишется
    и в API-контуре — та же apps.iam.services, что и Web."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()

    def test_wrong_password_writes_session_login_failed(self):
        self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "wrong",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")

    def test_correct_login_writes_no_failed_entry(self):
        self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED).exists()
        )


class FailedTotpAuditApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret = generate_totp_secret()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def test_wrong_totp_code_writes_session_login_failed(self):
        obtain = self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": obtain.data["ticket"], "code": "000000",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")

    def test_garbage_ticket_writes_session_login_failed_without_actor(self):
        self.client.post(reverse("iam_api:token-verify-totp"), {
            "ticket": "not-a-real-ticket", "code": "000000",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "")


class TokenObtainSessionLoginDetailsTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user(role=User.Role.SECURITY_OFFICER)

    def test_login_details_contain_full_name_and_role(self):
        self.client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entry.details["full_name"], self.user.full_name)
        self.assertEqual(entry.details["role"], User.Role.SECURITY_OFFICER)
        self.assertIn("ip_address", entry.details)
