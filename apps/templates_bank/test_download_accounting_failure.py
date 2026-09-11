"""Регрессии учёта скачивания при отказе object storage."""

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.documents.tests.test_permissions import make_user

from .models import Template
from .tests import PASSWORD, _make_template


class FailedDownloadAccountingTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0340")
        self.template = _make_template(
            status=Template.Status.SUPERSEDED,
            version="v-storage-fail",
        )
        self.template.file_editable = "templates/editable/2026/missing.docx"
        self.template.save(update_fields=["file_editable"])
        self.client = Client()
        self.client.login(personnel_number="0340", password=PASSWORD)

    @patch("django.db.models.fields.files.FieldFile.open", side_effect=OSError("storage unavailable"))
    def test_storage_open_failure_is_not_counted_or_audited(self, _open):
        """Счётчик/ARCHIVE_DOWNLOAD появляются только после доступности файла."""
        with self.assertRaises(OSError):
            self.client.get(
                reverse(
                    "templates_bank:download",
                    args=[self.template.pk, "file_editable"],
                )
            )

        self.template.refresh_from_db()
        self.assertEqual(self.template.download_count, 0)
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD,
                object_id=str(self.template.pk),
            ).exists()
        )
