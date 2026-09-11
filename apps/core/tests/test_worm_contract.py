import io
from unittest.mock import patch

from celery.exceptions import Retry
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from apps.core.models import StagedFilePromotion
from apps.documents.retention import RetentionCategory
from apps.documents.tasks import run_ocr_for_document
from apps.documents.tests.factories import make_document


class WormRetentionSnapshotTests(TestCase):
    def test_promotion_keeps_retention_snapshot_when_card_category_changes_later(self):
        working = InMemoryStorage()
        document = make_document(
            reg_number="WORM-SNAPSHOT",
            retention_category=RetentionCategory.DIRECTIVES_OPERATIONAL,
            reg_date=timezone.localdate(),
        )
        with (
            patch("apps.core.staged_files.working_storage", return_value=working),
            patch("apps.core.antivirus.needs_scan", return_value=False),
        ):
            document.files_original = SimpleUploadedFile(
                "snapshot.pdf", b"snapshot", content_type="application/pdf"
            )
            document.save()

        promotion = StagedFilePromotion.objects.get(
            model_label="documents.normativedocument",
            object_id=str(document.pk),
            field_name="files_original",
        )
        self.assertEqual(promotion.lock_mode, "GOVERNANCE")
        original_retain_until = promotion.retain_until
        self.assertIsNotNone(original_retain_until)

        # Legal classification changes later are a separate DB/audit action.
        # They must not mutate the already committed S3-copy intent.
        document.retention_category = RetentionCategory.ORDERS_CORE
        document.save(update_fields=["retention_category"])
        promotion.refresh_from_db()
        self.assertEqual(promotion.lock_mode, "GOVERNANCE")
        self.assertEqual(promotion.retain_until, original_retain_until)
        self.assertFalse(promotion.legal_hold)


class OcrPromotionOrderingTests(TestCase):
    def test_ocr_arriving_before_promotion_retries_then_completes_after_promotion(self):
        document = make_document(
            reg_number="WORM-OCR-RACE",
            files_original="documents/originals/2026/09/race.pdf",
        )
        promotion = StagedFilePromotion.objects.create(
            model_label="documents.normativedocument",
            object_id=str(document.pk),
            field_name="files_original",
            staging_name="staging/worm/race.pdf",
            destination_name=document.files_original.name,
        )

        # Message ordering is intentionally inverted: OCR reaches a worker first.
        # It must yield via Celery retry and must not open the nonexistent final key.
        with patch("django.db.models.fields.files.FieldFile.open") as open_file:
            with self.assertRaises(Retry):
                run_ocr_for_document.run(str(document.pk))
            open_file.assert_not_called()

        # Promotion never waits for OCR. Simulate the independently completed
        # promotion intent; the next OCR delivery can then consume originals.
        promotion.completed_at = timezone.now()
        promotion.sha256 = "a" * 64
        promotion.save(update_fields=["completed_at", "sha256"])

        with (
            patch(
                "django.db.models.fields.files.FieldFile.open",
                return_value=io.BytesIO(b"%PDF-1.4 race"),
            ),
            patch(
                "apps.documents.tasks.extract_text_and_confidence",
                return_value=("распознанный текст", 99.0),
            ),
        ):
            run_ocr_for_document.run(str(document.pk))

        document.refresh_from_db()
        self.assertEqual(document.ocr_body, "распознанный текст")
        self.assertEqual(document.ocr_status, document.OcrStatus.INDEXED)
