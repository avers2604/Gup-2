"""Административный сброс 2FA (по запросу ревью анти-фрода) —
apps/iam/services.reset_totp() + UserAdmin.reset_totp action."""
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog

from .. import services
from ..models import Department, User
from ..totp import generate_totp_secret


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


def _make_admin(personnel_number="9999"):
    admin_user = _make_user(
        personnel_number=personnel_number, role=User.Role.ADMINISTRATOR,
        is_staff=True, is_superuser=True,
    )
    return admin_user


class ResetTotpServiceTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.user.totp_secret = generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        self.admin = _make_admin()

    def test_reset_clears_secret_and_disables_totp(self):
        services.reset_totp(self.user, actor=self.admin)
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, "")
        self.assertEqual(self.user.totp_secret_encrypted, "")
        self.assertEqual(self.user.totp_secret_plaintext, "")
        self.assertFalse(self.user.totp_enabled)

    def test_reset_writes_audit_entry(self):
        services.reset_totp(self.user, actor=self.admin)
        entry = AuditLog.objects.filter(
            event_type=AuditLog.EventType.USER_TOTP_RESET,
        ).latest("created_at")
        self.assertEqual(entry.actor_personnel_number, self.admin.personnel_number)
        self.assertEqual(entry.details["target_personnel_number"], self.user.personnel_number)

    def test_reset_allows_fresh_enrollment_afterwards(self):
        services.reset_totp(self.user, actor=self.admin)
        self.user.refresh_from_db()
        enrollment = services.start_totp_enrollment(self.user)
        self.assertTrue(enrollment["secret"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.totp_secret, enrollment["secret"])


class ResetTotpAdminActionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = _make_admin()
        self.user = _make_user()
        self.user.totp_secret = generate_totp_secret()
        self.user.totp_enabled = True
        self.user.save()
        self.client.force_login(self.admin)

    def test_action_resets_selected_user_totp(self):
        url = reverse("admin:iam_user_changelist")
        response = self.client.post(url, {
            "action": "reset_totp",
            "_selected_action": [str(self.user.pk)],
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertFalse(self.user.totp_enabled)
        self.assertEqual(self.user.totp_secret, "")
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.USER_TOTP_RESET,
                object_id=str(self.user.pk),
            ).exists()
        )
