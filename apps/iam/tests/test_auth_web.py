"""Web GUI (apps/iam/views.py) — серверный рендеринг, сессия + CSRF."""
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog

from ..models import Department, User
from ..totp import generate_totp_secret


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


class LoginPageTests(TestCase):
    def test_login_page_renders(self):
        response = self.client.get(reverse("iam:login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "iam/login.html")


class LoginWithoutTotpTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_user()

    def test_correct_credentials_log_in_and_redirect(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertRedirects(response, reverse("search_ocr:search"))

    def test_session_is_authenticated_after_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        # totp-enroll требует LoginRequiredMixin — косвенная проверка,
        # что сессия реально авторизована.
        response = self.client.get(reverse("iam:totp-enroll"))
        self.assertEqual(response.status_code, 200)

    def test_login_writes_audit_entry(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN).count(), 1,
        )

    def test_wrong_password_rerenders_form_with_error(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "wrong",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный табельный номер или пароль.")
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN).exists()
        )

    def test_wrong_password_does_not_authenticate_session(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "wrong",
        })
        response = self.client.get(reverse("iam:totp-enroll"))
        self.assertNotEqual(response.status_code, 200)

    def test_blocked_user_cannot_log_in(self):
        _make_user(personnel_number="0002", status=User.Status.BLOCKED)
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0002", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный табельный номер или пароль.")


class LoginWithTotpTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.secret = generate_totp_secret()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def _current_code(self):
        import pyotp

        return pyotp.TOTP(self.secret).now()

    def test_login_with_totp_enabled_redirects_to_verify_page_not_authenticated(self):
        response = self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertRedirects(response, reverse("iam:login-verify-totp"))

        enroll_response = self.client.get(reverse("iam:totp-enroll"))
        self.assertNotEqual(enroll_response.status_code, 200)

    def test_verify_page_without_pending_login_redirects_to_login(self):
        response = self.client.get(reverse("iam:login-verify-totp"))
        self.assertRedirects(response, reverse("iam:login"))

    def test_correct_totp_code_completes_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": self._current_code()})
        self.assertRedirects(response, reverse("search_ocr:search"))

        enroll_response = self.client.get(reverse("iam:totp-enroll"))
        self.assertEqual(enroll_response.status_code, 200)

    def test_wrong_totp_code_rerenders_form_with_error(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный код.")
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN).exists()
        )

    def test_user_blocked_between_steps_cannot_complete_login(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.user.status = User.Status.BLOCKED
        self.user.save()

        response = self.client.post(reverse("iam:login-verify-totp"), {"code": self._current_code()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный код.")


class LogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })

    def test_logout_ends_session(self):
        response = self.client.post(reverse("iam:logout"))
        self.assertRedirects(response, reverse("iam:login"))

        enroll_response = self.client.get(reverse("iam:totp-enroll"))
        self.assertNotEqual(enroll_response.status_code, 200)

    def test_logout_writes_audit_entry(self):
        self.client.post(reverse("iam:logout"))
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGOUT).count(), 1,
        )

    def test_logout_via_get_not_allowed(self):
        response = self.client.get(reverse("iam:logout"))
        self.assertEqual(response.status_code, 405)

    def test_logout_requires_authentication(self):
        anonymous_client = Client()
        response = anonymous_client.post(reverse("iam:logout"))
        self.assertRedirects(response, f"{reverse('iam:login')}?next={reverse('iam:logout')}")


class TotpEnrollmentWebTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })

    def test_enroll_requires_login(self):
        response = Client().get(reverse("iam:totp-enroll"))
        self.assertEqual(response.status_code, 302)

    def test_enroll_post_returns_secret_and_confirm_form(self):
        response = self.client.post(reverse("iam:totp-enroll"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "iam/_totp_enroll_result.html")

        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_secret)
        self.assertFalse(self.user.totp_enabled)

    def test_confirm_with_correct_code_enables_2fa(self):
        import pyotp

        self.client.post(reverse("iam:totp-enroll"))
        self.user.refresh_from_db()
        code = pyotp.TOTP(self.user.totp_secret).now()

        response = self.client.post(reverse("iam:totp-confirm"), {"code": code})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2FA включена")

        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_confirm_with_wrong_code_does_not_enable_2fa(self):
        self.client.post(reverse("iam:totp-enroll"))
        response = self.client.post(reverse("iam:totp-confirm"), {"code": "000000"})
        self.assertEqual(response.status_code, 400)

        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)

    def test_confirm_without_enroll_first_rejected(self):
        response = self.client.post(reverse("iam:totp-confirm"), {"code": "123456"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Подключение не начато", status_code=400)


class FailedLoginAuditTests(TestCase):
    """Усиление аудита (решение Заказчика): неудачные попытки входа
    пишутся в SESSION_LOGIN_FAILED — основа Grafana-алерта «5+ подряд»."""

    def setUp(self):
        self.client = Client()
        self.user = _make_user()

    def test_wrong_password_writes_session_login_failed(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "wrong",
        })
        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")

    def test_blocked_user_login_writes_session_login_failed(self):
        _make_user(personnel_number="0002", status=User.Status.BLOCKED)
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0002", "password": "Sup3r$ecret!Pass",
        })
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.SESSION_LOGIN_FAILED, actor_personnel_number="0002",
            ).exists()
        )

    def test_correct_login_writes_no_failed_entry(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertFalse(
            AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED).exists()
        )


class FailedTotpAuditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.secret = generate_totp_secret()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def test_wrong_totp_code_writes_session_login_failed(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.client.post(reverse("iam:login-verify-totp"), {"code": "000000"})

        entries = AuditLog.objects.filter(event_type=AuditLog.EventType.SESSION_LOGIN_FAILED)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().actor_personnel_number, "0001")


class SessionLoginDetailsTests(TestCase):
    """Усиление аудита: SESSION_LOGIN несёт снимок ФИО/роли/IP, не только
    табельный номер."""

    def setUp(self):
        self.client = Client()
        self.user = _make_user(role=User.Role.SECURITY_OFFICER)

    def test_login_details_contain_full_name_and_role(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        entry = AuditLog.objects.get(event_type=AuditLog.EventType.SESSION_LOGIN)
        self.assertEqual(entry.details["full_name"], self.user.full_name)
        self.assertEqual(entry.details["role"], User.Role.SECURITY_OFFICER)
        self.assertIn("ip_address", entry.details)


class SharedTerminalSessionTimeoutTests(TestCase):
    """Таймаут неактивности: 30 минут по умолчанию, 15 — если отмечен
    терминал общего доступа (решение Заказчика)."""

    def setUp(self):
        self.client = Client()
        self.user = _make_user()

    def test_shared_terminal_checkbox_shortens_session_to_15_minutes(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
            "shared_terminal": "on",
        })
        self.assertEqual(self.client.session.get_expiry_age(), 15 * 60)

    def test_without_shared_terminal_uses_default_30_minutes(self):
        self.client.post(reverse("iam:login"), {
            "personnel_number": "0001", "password": "Sup3r$ecret!Pass",
        })
        self.assertEqual(self.client.session.get_expiry_age(), 30 * 60)
