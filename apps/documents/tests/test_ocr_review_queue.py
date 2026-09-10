from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.core.models import OcrReviewQueueEntry
from apps.documents.models import NormativeDocument
from apps.documents.tasks import run_ocr_for_document

from .factories import make_document


def _mock_storage_open(content=b"%PDF-1.4 fake"):
    fake_file = MagicMock()
    fake_file.read.return_value = content
    return patch("django.core.files.storage.FileSystemStorage.open", return_value=fake_file)


class OcrReviewQueueTests(TestCase):
    def test_low_confidence_creates_stable_review_queue_entry(self):
        document = make_document(
            reg_number="OCR-QUEUE-1",
            files_original="documents/originals/2026/01/queue-1.pdf",
            ocr_category=NormativeDocument.OcrCategory.MODERN_NRD,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("Текст", 80.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(document.pk))

        entry = OcrReviewQueueEntry.objects.get(document_id=document.pk)
        first_required_at = entry.required_at

        # Repeated low-quality delivery must not reset the age of the queue.
        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("Текст 2", 81.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(document.pk))

        entry.refresh_from_db()
        self.assertEqual(entry.required_at, first_required_at)

    def test_successful_rerun_removes_review_queue_entry(self):
        document = make_document(
            reg_number="OCR-QUEUE-2",
            files_original="documents/originals/2026/01/queue-2.pdf",
            ocr_category=NormativeDocument.OcrCategory.MODERN_NRD,
        )
        OcrReviewQueueEntry.objects.create(document_id=document.pk, required_at=document.created_at)
        NormativeDocument.objects.filter(pk=document.pk).update(
            ocr_status=NormativeDocument.OcrStatus.NEEDS_REVIEW
        )

        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("Качественный текст", 95.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(document.pk))

        self.assertFalse(OcrReviewQueueEntry.objects.filter(document_id=document.pk).exists())
