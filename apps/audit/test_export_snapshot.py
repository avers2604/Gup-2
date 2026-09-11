"""Регрессии консистентности снимка CSV-выгрузки WORM-журнала."""

from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.tests.test_permissions import make_user
from apps.iam.models import User

from .models import AuditLog

PASSWORD = "Sup3r$ecret!Pass"


class AuditExportSnapshotTests(TestCase):
    def setUp(self):
        self.officer = make_user(
            personnel_number="0490",
            role=User.Role.SECURITY_OFFICER,
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0999",
            object_type="NormativeDocument",
            object_id="SNAPSHOT-1",
        )
        self.client = Client()
        self.client.login(personnel_number="0490", password=PASSWORD)

    @staticmethod
    def _body(response):
        return b"".join(response.streaming_content).decode("utf-8")

    def test_export_does_not_include_its_own_audit_event(self):
        """Срез фиксируется до самофиксации выгрузки и не меняется при стриминге."""
        response = self.client.get(reverse("audit:export"))
        body = self._body(response)

        self.assertIn("SNAPSHOT-1", body)
        self.assertNotIn(
            "Экспорт журнала аудита",
            body,
            "событие текущей выгрузки создано после снимка и не должно попасть в тот же CSV",
        )

        event = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).latest("created_at")
        self.assertEqual(event.details["matched_entries"], 1)

    def test_invalid_filters_do_not_fall_back_to_full_export(self):
        before = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).count()

        response = self.client.get(
            reverse("audit:export"),
            {"date_from": "2030-01-01", "date_to": "2020-01-01"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            AuditLog.objects.filter(event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED).count(),
            before,
        )
