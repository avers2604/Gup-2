from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.audit.models import AuditLog
from apps.documents.models import NormativeDocument
from apps.documents.tasks import run_ocr_for_document

from .factories import make_document


def _mock_storage_open(content=b"%PDF-1.4 fake"):
    fake_file = MagicMock()
    fake_file.read.return_value = content
    return patch(
        "django.core.files.storage.FileSystemStorage.open",
        return_value=fake_file,
    )


class OcrRetryBudgetIsolationTests(TestCase):
    """Ожидание WORM promotion не должно съедать ретраи самого OCR."""

    def test_transient_ocr_failure_still_retries_after_promotion_waits(self):
        document = make_document(
            reg_number="OCR-BUDGET",
            files_original="documents/originals/2026/01/retry-budget.pdf",
        )

        with patch(
            "apps.core.staged_files.promotion_pending_for",
            side_effect=[True, True, True, True, True, False, False],
        ), patch(
            "apps.documents.tasks.extract_text_and_confidence",
            side_effect=[RuntimeError("временная ошибка Tesseract"), ("Восстановлено", 95.0)],
        ), _mock_storage_open():
            run_ocr_for_document.apply(args=[str(document.pk)], throw=False)

        document.refresh_from_db()
        self.assertEqual(document.ocr_body, "Восстановлено")
        self.assertEqual(document.ocr_status, NormativeDocument.OcrStatus.INDEXED)
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
                object_id=str(document.pk),
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED,
                object_id=str(document.pk),
            ).exists()
        )
