"""Регрессии смены грифа доступа во время выдачи ссылки на файл НРД."""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditLog

from ..models import NormativeDocument
from .factories import make_document
from .test_permissions import make_user


class FileLinkAccessRaceTests(TestCase):
    def _document(self, reg_number):
        return make_document(
            reg_number=reg_number,
            access_level=NormativeDocument.AccessLevel.GENERAL,
            files_original=f"documents/originals/2026/01/{reg_number}.pdf",
        )

    @staticmethod
    def _flip_to_restricted(document):
        def side_effect(*args, **kwargs):
            NormativeDocument.objects.filter(pk=document.pk).update(
                access_level=NormativeDocument.AccessLevel.RESTRICTED,
            )
            return "/media/generated-link.pdf"

        return side_effect

    def test_link_is_not_released_if_document_becomes_restricted(self):
        """Пользователь без ДСП не получает URL после смены грифа в гонке."""
        user = make_user(personnel_number="0610", dsp_access=False)
        document = self._document("RACE-FILE-1")
        self.client.force_login(user)

        with patch(
            "django.core.files.storage.FileSystemStorage.url",
            side_effect=self._flip_to_restricted(document),
        ):
            response = self.client.get(
                reverse("documents:file-link", args=[document.pk, "original"])
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.EXPORT_RESTRICTED,
                object_id=str(document.pk),
            ).exists()
        )

    def test_current_restricted_status_drives_export_audit(self):
        """Допущенная выдача после смены грифа обязана попасть в WORM."""
        user = make_user(personnel_number="0611", dsp_access=True)
        document = self._document("RACE-FILE-2")
        self.client.force_login(user)

        with patch(
            "django.core.files.storage.FileSystemStorage.url",
            side_effect=self._flip_to_restricted(document),
        ):
            response = self.client.get(
                reverse("documents:file-link", args=[document.pk, "original"]),
                REMOTE_ADDR="203.0.113.61",
            )

        self.assertEqual(response.status_code, 302)
        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.EXPORT_RESTRICTED,
            object_id=str(document.pk),
        )
        self.assertEqual(entry.actor_personnel_number, "0611")
        self.assertEqual(entry.details["reg_number"], "RACE-FILE-2")
        self.assertEqual(entry.details["kind"], "original")
