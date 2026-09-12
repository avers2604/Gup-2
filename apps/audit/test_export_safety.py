from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.documents.tests.test_permissions import PASSWORD, make_user
from apps.iam.models import User

from .models import AuditLog


class AuditExportSafetyTests(TestCase):
    """Выгрузка журнала — защищённая операция, а не побочный эффект GET."""

    def setUp(self):
        self.officer = make_user(
            personnel_number="0491", role=User.Role.SECURITY_OFFICER
        )
        self.client_ = Client()
        self.client_.login(
            personnel_number=self.officer.personnel_number, password=PASSWORD
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor_personnel_number="0888",
            object_type="User",
            object_id="export-safety-1",
        )

    def test_get_does_not_start_or_record_an_export(self):
        before = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).count()

        response = self.client_.get(reverse("audit:export"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
            ).count(),
            before,
        )

    def test_post_downloads_export(self):
        response = self.client_.post(reverse("audit:export"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("export-safety-1", body)

    @override_settings(AUDIT_EXPORT_MAX_ROWS=1)
    def test_export_above_configured_row_limit_is_rejected(self):
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor_personnel_number="0777",
            object_type="User",
            object_id="export-safety-2",
        )
        before = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).count()

        response = self.client_.post(reverse("audit:export"))

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
            ).count(),
            before,
        )
