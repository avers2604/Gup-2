"""Регрессия проверки ДСП-доступа после блокировки карточки OCR."""

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.core.models import OcrReviewQueueEntry
from apps.documents import services
from apps.documents.models import NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import make_user


class OcrReviewAccessRaceTests(TestCase):
    def setUp(self):
        self.methodist = make_user(
            personnel_number="0530",
            role=User.Role.METHODIST,
            dsp_access=False,
        )
        self.document = make_document(
            reg_number="OCR-ACCESS-RACE",
            access_level=NormativeDocument.AccessLevel.GENERAL,
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW,
            ocr_body="исходный текст",
        )
        OcrReviewQueueEntry.objects.create(
            document_id=self.document.pk,
            required_at=timezone.now(),
        )

    def test_permission_is_rechecked_on_locked_current_document(self):
        """Устаревший GENERAL-объект не даёт записать OCR после перевода в ДСП."""
        # self.document остаётся устаревшим GENERAL-экземпляром — ровно это
        # происходит, если доступ изменился после чтения карточки, но до записи.
        NormativeDocument.objects.filter(pk=self.document.pk).update(
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )

        with self.assertRaises(PermissionDenied):
            services.apply_ocr_review(
                actor=self.methodist,
                document=self.document,
                corrected_text="секретный исправленный текст",
            )

        self.document.refresh_from_db()
        self.assertEqual(self.document.access_level, NormativeDocument.AccessLevel.RESTRICTED)
        self.assertEqual(self.document.ocr_body, "исходный текст")
        self.assertEqual(self.document.ocr_status, NormativeDocument.OcrStatus.NEEDS_REVIEW)
        self.assertTrue(
            OcrReviewQueueEntry.objects.filter(document_id=self.document.pk).exists()
        )
