"""Регрессии изоляции legacy WORM-событий в карточке НРД."""

from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.documents.models import NormativeDocument

from .factories import make_document
from .test_permissions import PASSWORD, make_user


class LegacyActivityIsolationTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0720")
        self.client = Client()
        self.client.login(personnel_number="0720", password=PASSWORD)

    def test_duplicate_reg_number_does_not_mix_legacy_activity_from_hidden_card(self):
        """Неоднозначные legacy-события по reg_number безопаснее не показывать."""
        visible = make_document(reg_number="ДУБЛЬ-77")
        make_document(
            reg_number="ДУБЛЬ-77",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )

        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_STATUS_CHANGED,
            object_type="NormativeDocument",
            object_id=str(visible.pk),
            details={"comment": "VISIBLE-UUID-EVENT"},
        )
        AuditLog.objects.create(
            event_type=AuditLog.EventType.DOCUMENT_STATUS_CHANGED,
            object_type="NormativeDocument",
            object_id="ДУБЛЬ-77",
            details={"comment": "SECRET-DSP-LEGACY-EVENT"},
        )

        response = self.client.get(reverse("documents:detail", args=[visible.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VISIBLE-UUID-EVENT")
        self.assertNotContains(response, "SECRET-DSP-LEGACY-EVENT")
