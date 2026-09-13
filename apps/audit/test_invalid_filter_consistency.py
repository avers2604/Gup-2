from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.tests.test_permissions import PASSWORD, make_user
from apps.iam.models import User

from .models import AuditLog


class AuditInvalidFilterConsistencyTests(TestCase):
    """Невалидный фильтр не должен расширять экран до полного WORM-журнала."""

    def setUp(self):
        self.officer = make_user(
            personnel_number="0490", role=User.Role.SECURITY_OFFICER
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_PUBLISHED,
            actor_personnel_number="0999",
            object_type="NormativeDocument",
            object_id="FILTER-1",
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.SESSION_LOGIN,
            actor_personnel_number="0888",
            object_type="User",
            object_id="filter-user",
        )
        self.client_ = Client()
        self.client_.login(
            personnel_number=self.officer.personnel_number, password=PASSWORD
        )

    def test_invalid_filter_fails_closed_on_screen_like_export(self):
        params = {
            "date_from": "2026-02-31",
            "event_type": AuditLog.EventType.DOCUMENT_PUBLISHED,
        }

        response = self.client_.get(reverse("audit:list"), params)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertEqual(list(response.context["entries"]), [])

        export = self.client_.get(reverse("audit:export"), params)
        self.assertEqual(export.status_code, 400)
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.AUDIT_LOG_EXPORTED
            ).exists()
        )
