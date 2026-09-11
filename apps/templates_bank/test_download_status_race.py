"""Регрессия учёта скачивания при конкурентном supersede версии бланка."""

from django.test import TestCase

from apps.audit.models import AuditLog
from apps.documents.tests.test_permissions import make_user

from .models import Template
from .tests import _make_template
from . import services


class DownloadStatusRaceTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0321")
        self.template = _make_template(version="v9.1")
        self.template.file_editable = "templates/editable/2026/form-race.docx"
        self.template.save(update_fields=["file_editable"])

    def test_archive_audit_uses_current_status_not_stale_instance(self):
        """Stale ACTIVE не должен скрыть архивную выдачу после supersede."""
        # Экземпляр self.template остаётся ACTIVE, но актуальная строка уже
        # SUPERSEDED — это окно между открытием файла и register_download().
        Template.objects.filter(pk=self.template.pk).update(
            status=Template.Status.SUPERSEDED,
        )

        services.register_download(
            actor=self.user,
            template=self.template,
            field_name="file_editable",
        )

        self.template.refresh_from_db()
        self.assertEqual(self.template.download_count, 1)
        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.ARCHIVE_DOWNLOAD,
            object_id=str(self.template.pk),
        )
        self.assertEqual(entry.details["version"], "v9.1")
        self.assertEqual(entry.actor_personnel_number, "0321")
