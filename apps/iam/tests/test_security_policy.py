from unittest.mock import patch

import pyotp
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.iam import services
from apps.iam.models import User
from apps.iam.tests.test_auth_web import _make_user


class AccountPolicyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user()

    def test_admin_without_factor_is_restricted_to_enrollment(self):
        self.user.role = User.Role.ADMINISTRATOR
        self.user.save()
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/documents/"), reverse("iam:totp-enroll"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/admin/"), reverse("iam:totp-enroll"), fetch_redirect_response=False)

    def test_standard_admin_password_login_is_disabled(self):
        self.assertRedirects(self.client.post("/admin/login/", {}), reverse("iam:login"), fetch_redirect_response=False)

    def test_password_only_session_cannot_bypass_enabled_factor(self):
        self.user.totp_secret = pyotp.random_base32()
        self.user.totp_enabled = True
        self.user.save()
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/documents/"), reverse("iam:login"), fetch_redirect_response=False)

    def test_api_restricts_account_requiring_password_change(self):
        self.user.status = User.Status.PASSWORD_CHANGE_REQUIRED
        self.user.save()
        client = APIClient()
        tokens = client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": self.user.personnel_number, "password": "Sup3r$ecret!Pass"}).data
        client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
        self.assertEqual(client.get(reverse("iam_api:me")).status_code, 403)
        self.assertEqual(APIClient().post(reverse("iam_api:token-refresh"), {"refresh": tokens["refresh"]}).status_code, 401)

    def test_password_change_revokes_existing_jwt(self):
        client = APIClient()
        tokens = client.post(reverse("iam_api:token-obtain"), {
            "personnel_number": self.user.personnel_number, "password": "Sup3r$ecret!Pass"}).data
        self.user.set_password("New$ecretPassword99")
        self.user.save(update_fields=["password"])
        client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
        self.assertEqual(client.get(reverse("iam_api:me")).status_code, 401)
        self.assertEqual(APIClient().post(reverse("iam_api:token-refresh"), {"refresh": tokens["refresh"]}).status_code, 401)

    def test_status_only_save_persists_active_flag(self):
        self.user.status = User.Status.BLOCKED
        self.user.save(update_fields=["status"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_partial_save_does_not_publish_unsaved_role(self):
        self.user.role = User.Role.ADMINISTRATOR
        with patch("apps.iam.models.publish") as publish:
            self.user.save(update_fields=["last_name"])
        publish.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, User.Role.READER)


class TotpReplayTests(TestCase):
    def setUp(self):
        self.secret = pyotp.random_base32()
        self.user = _make_user(totp_enabled=True, totp_secret=self.secret)

    def test_enabled_factor_cannot_be_replaced_by_enrollment(self):
        with self.assertRaises(PermissionDenied):
            services.start_totp_enrollment(self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, self.secret)

    def test_same_code_cannot_be_used_with_another_ticket(self):
        ticket = services.make_totp_pending_ticket(self.user)
        code = pyotp.TOTP(self.secret).now()
        self.assertIsNotNone(services.verify_totp_login(ticket=ticket, code=code))
        self.assertIsNone(services.verify_totp_login(ticket=services.make_totp_pending_ticket(self.user), code=code))

    def test_consumed_ticket_cannot_be_reused_with_next_code(self):
        ticket = services.make_totp_pending_ticket(self.user)
        self.assertIsNotNone(services.verify_totp_login(ticket=ticket, code=pyotp.TOTP(self.secret).now()))
        next_code = pyotp.TOTP(self.secret).at(timezone.now().timestamp() + 30)
        self.assertIsNone(services.verify_totp_login(ticket=ticket, code=next_code))

    def test_password_change_invalidates_pending_ticket(self):
        ticket = services.make_totp_pending_ticket(self.user)
        self.user.set_password("Different$Password99")
        self.user.save()
        self.assertIsNone(services.verify_totp_login(ticket=ticket, code=pyotp.TOTP(self.secret).now()))


class AtomicUserTests(TransactionTestCase):
    def test_audit_failure_rolls_back_role_change_without_outer_transaction(self):
        user = _make_user()
        user.role = User.Role.ADMINISTRATOR
        with patch("apps.iam.models.publish", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                user.save()
        user.refresh_from_db()
        self.assertEqual(user.role, User.Role.READER)
