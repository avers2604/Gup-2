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


class LoginWithoutTotpTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()

    def test_correct_credentials_log_in_directly(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["totp_required"])
        self.assertEqual(response.data["personnel_number"], "0001")

    def test_session_is_authenticated_after_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        # /totp/enroll/ требует IsAuthenticated — косвенная проверка, что
        # сессия реально авторизована, не только вернула 200 на /login/.
        response = self.client.post(reverse("iam:totp-enroll"))
        self.assertEqual(response.status_code, 200)

    def test_login_writes_session_login_audit_entry(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")

    def test_wrong_password_rejected_with_generic_message(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN).exists()
        )

    def test_unknown_personnel_number_rejected_with_same_message_as_wrong_password(self):
        response_unknown = self.client.post(reverse("iam:login"), {
            "personnel_number": "9999999", "password": "whatever",
        })
        response_wrong_pw = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "wrong",
        })
        self.assertEqual(response_unknown.status_code, 401)
        self.assertEqual(response_unknown.data["detail"], response_wrong_pw.data["detail"])

    def test_blocked_user_cannot_log_in(self):
        _make_user(personnel_number="0002", status=User.Status.BLOCKED)
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0002", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 401)

    def test_must_enroll_totp_flag_for_role_that_requires_it(self):
        _make_user(personnel_number="0003", role=User.Role.CURATOR)
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0003", "password": "Sup3r$ecret!Pass",
        })
        self.assertTrue(response.data["must_enroll_totp"])

    def test_must_enroll_totp_false_for_role_without_requirement(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertFalse(response.data["must_enroll_totp"])

    def test_password_change_required_flag_surfaced(self):
        _make_user(personnel_number="0004", status=User.Status.PASSWORD_CHANGE_REQUIRED)
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0004", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["password_change_required"])


class LoginWithTotpTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret = generate_totp_secret()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def _current_code(self):
        import pyotp

        return pyotp.TOTP(self.secret).now()

    def test_login_with_totp_enabled_does_not_authenticate_yet(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["totp_required"])

        # Сессия существует (пометка "ожидает код"), но НЕ авторизована —
        # запрос к защищённому эндпоинту должен упасть.
        enroll_response = self.client.post(reverse("iam:totp-enroll"))
        self.assertEqual(enroll_response.status_code, 403)

    def test_correct_totp_code_completes_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": self._current_code()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["personnel_number"], "0001")

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entries.count(), 1)

    def test_wrong_totp_code_does_not_complete_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": "000000"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN).exists()
        )

    def test_totp_verify_without_prior_login_step_rejected(self):
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": "000000"})
        self.assertEqual(response.status_code, 400)

    def test_user_blocked_between_steps_cannot_complete_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.user.status = User.Status.BLOCKED
        self.user.save()

        response = self.client.post(reverse("iam:login-verify-totp"), {"code": self._current_code()})
        self.assertEqual(response.status_code, 401)


class TotpEnrollmentTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })

    def test_enroll_generates_secret_without_enabling_2fa(self):
        response = self.client.post(reverse("iam:totp-enroll"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("secret", response.data)
        self.assertIn("provisioning_uri", response.data)

        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_secret)
        self.assertFalse(self.user.totp_enabled)

    def test_confirm_with_correct_code_enables_2fa(self):
        import pyotp

        enroll_response = self.client.post(reverse("iam:totp-enroll"))
        secret = enroll_response.data["secret"]
        code = pyotp.TOTP(secret).now()

        response = self.client.post(reverse("iam:totp-confirm"), {"code": code})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["totp_enabled"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_confirm_with_wrong_code_does_not_enable_2fa(self):
        self.client.post(reverse("iam:totp-enroll"))
        response = self.client.post(reverse("iam:totp-confirm"), {"code": "000000"})
        self.assertEqual(response.status_code, 401)

        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)

    def test_confirm_without_enroll_first_rejected(self):
        response = self.client.post(reverse("iam:totp-confirm"), {"code": "123456"})
        self.assertEqual(response.status_code, 400)


class LogoutTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })

    def test_logout_ends_session(self):
        response = self.client.post(reverse("iam:logout"))
        self.assertEqual(response.status_code, 204)

        enroll_response = self.client.post(reverse("iam:totp-enroll"))
        self.assertEqual(enroll_response.status_code, 403)

    def test_logout_writes_audit_entry(self):
        self.client.post(reverse("iam:logout"))
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGOUT)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")

    def test_logout_requires_authentication(self):
        anonymous_client = APIClient()
        response = anonymous_client.post(reverse("iam:logout"))
        self.assertEqual(response.status_code, 403)
