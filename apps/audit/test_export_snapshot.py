import csv
import io

from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.tests.test_permissions import PASSWORD, make_user
from apps.iam.models import User

from .models import AuditLog


class AuditLogExportSnapshotTests(TestCase):
    def test_streamed_row_count_matches_audited_match_count(self):
        officer = make_user(personnel_number="0490", role=User.Role.SECURITY_OFFICER)
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0999",
            object_type="NormativeDocument",
            object_id="snapshot-probe",
        )
        client = Client()
        client.login(personnel_number=officer.personnel_number, password=PASSWORD)

        response = client.get(reverse("audit:export"))
        body = b"".join(response.streaming_content).decode("utf-8")
        export_event = AuditLog.objects.filter(
            event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
        ).latest("created_at")
        rows = list(csv.reader(io.StringIO(body.lstrip("\ufeff")), delimiter=";"))

        self.assertEqual(len(rows) - 1, export_event.details["matched_entries"])
