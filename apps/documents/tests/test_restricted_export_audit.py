"""Выдача файла документа с грифом ДСП оставляет след в WORM-журнале.

Для бланков аналогичное событие (`ARCHIVE_DOWNLOAD`) писалось с Этапа 2, а
для ДСП-документов `EXPORT_RESTRICTED` оставался заготовкой: доступ к самому
чувствительному материалу в системе был единственным, который нигде не
фиксировался.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.documents.models import NormativeDocument

from .factories import make_document
from .test_permissions import make_user


class RestrictedExportAuditTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0600", dsp_access=True)
        self.client.force_login(self.user)
        self.restricted = make_document(
            reg_number="ДСП-1",
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
            files_original="documents/originals/2026/01/secret.pdf",
        )
        self.general = make_document(
            reg_number="ОБЩ-1",
            files_original="documents/originals/2026/01/public.pdf",
        )

    def _entries(self):
        return AuditLog.objects.filter(event_type=AuditLog.EventType.EXPORT_RESTRICTED)

    def test_restricted_file_link_is_recorded(self):
        response = self.client.get(
            reverse("documents:file-link", args=[self.restricted.pk, "original"]),
            REMOTE_ADDR="203.0.113.44",
        )

        self.assertEqual(response.status_code, 302)
        entry = self._entries().get()
        self.assertEqual(entry.actor_personnel_number, "0600")
        self.assertEqual(entry.object_type, "NormativeDocument")
        self.assertEqual(entry.object_id, str(self.restricted.pk))
        self.assertEqual(entry.details["reg_number"], "ДСП-1")
        self.assertEqual(entry.details["kind"], "original")
        self.assertEqual(entry.details["ip_address"], "203.0.113.44")

    def test_presigned_url_never_reaches_the_log(self):
        """Ссылка действует как предъявительский пропуск к файлу.

        Журнал аудита читают шире, чем сам ДСП-документ, поэтому ссылка в
        нём означала бы обход грифа доступа через журнал.
        """
        response = self.client.get(
            reverse("documents:file-link", args=[self.restricted.pk, "original"])
        )

        entry = self._entries().get()
        self.assertNotIn(response["Location"], str(entry.details))
        self.assertNotIn("secret.pdf", str(entry.details))

    def test_general_document_is_not_recorded(self):
        """Иначе записи, ради которых журнал заводился, утонут в шуме."""
        self.client.get(reverse("documents:file-link", args=[self.general.pk, "original"]))

        self.assertFalse(self._entries().exists())

    def test_no_event_when_the_link_could_not_be_generated(self):
        """Неудача хранилища доступа не даёт — значит и выгрузки не было."""
        with patch(
            "django.core.files.storage.FileSystemStorage.url",
            side_effect=PermissionError("forbidden"),
        ):
            response = self.client.get(
                reverse("documents:file-link", args=[self.restricted.pk, "original"])
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._entries().exists())

    def test_user_without_clearance_gets_404_and_leaves_no_event(self):
        """404, а не 403: факт существования ДСП-документа тоже закрыт."""
        outsider = make_user(personnel_number="0601")
        self.client.force_login(outsider)

        response = self.client.get(
            reverse("documents:file-link", args=[self.restricted.pk, "original"])
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self._entries().exists())

    def test_editable_copy_is_recorded_too(self):
        self.restricted.files_editable = "documents/editable/2026/01/secret.docx"
        self.restricted.save(update_fields=["files_editable"])

        self.client.get(
            reverse("documents:file-link", args=[self.restricted.pk, "editable"])
        )

        self.assertEqual(self._entries().get().details["kind"], "editable")
