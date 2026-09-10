"""Тесты логики Celery-задачи run_ocr_for_document — extract_text_and_confidence
замокан (реальный OCR уже проверен в test_ocr.py), здесь проверяется
только оркестрация: обновление карточки, WORM-аудит, retry/финальный отказ.

CELERY_TASK_ALWAYS_EAGER=True в тестовом режиме (config/settings/base.py) —
.delay()/.apply() выполняются синхронно, без реального брокера Redis.
FileSystemStorage.open() подменяется фиктивным файлом — реальное чтение с
диска не нужно, оно уже проверено в apps/documents/tests/test_retention.py
и test_versioning.py для files_original в целом."""
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.audit.models import AuditLog
from apps.documents.models import NormativeDocument
from apps.documents.tasks import run_ocr_for_document

from .factories import make_document


def _mock_storage_open(content=b"%PDF-1.4 fake"):
    fake_file = MagicMock()
    fake_file.read.return_value = content
    return patch("django.core.files.storage.FileSystemStorage.open", return_value=fake_file)


class RunOcrForDocumentSuccessTests(TestCase):
    def test_updates_ocr_body_and_confidence(self):
        doc = make_document(reg_number="OCR-1", files_original="documents/originals/2026/01/ocr-1.pdf")

        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("Распознанный текст", 87.5),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_body, "Распознанный текст")
        self.assertEqual(doc.ocr_confidence, 87.5)

    def test_writes_ocr_completed_audit_entry(self):
        doc = make_document(reg_number="OCR-2", files_original="documents/originals/2026/01/ocr-2.pdf")

        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("Текст", 90.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED, object_id=str(doc.pk),
        )
        self.assertEqual(entry.details["confidence"], 90.0)

    def test_handles_no_recognized_text(self):
        doc = make_document(reg_number="OCR-BLANK", files_original="documents/originals/2026/01/blank.pdf")

        with patch(
            "apps.documents.tasks.extract_text_and_confidence",
            return_value=("", None),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_body, "")
        self.assertIsNone(doc.ocr_confidence)
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.NEEDS_REVIEW)
        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED, object_id=str(doc.pk),
            ).exists()
        )


class RunOcrForDocumentThresholdTests(TestCase):
    """Дифференцированные пороги качества по категории (ТЗ 2.2 §4.4.2,
    apps/documents/ocr_thresholds.py) — граница confidence < review_below."""

    def test_confidence_at_or_above_threshold_is_indexed(self):
        doc = make_document(
            reg_number="OCR-THR-1", files_original="documents/originals/2026/01/thr1.pdf",
            ocr_category=NormativeDocument.OcrCategory.MODERN_NRD,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence", return_value=("Текст", 90.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.INDEXED)

    def test_confidence_below_threshold_needs_review(self):
        doc = make_document(
            reg_number="OCR-THR-2", files_original="documents/originals/2026/01/thr2.pdf",
            ocr_category=NormativeDocument.OcrCategory.MODERN_NRD,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence", return_value=("Текст", 89.9),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.NEEDS_REVIEW)

    def test_archive_uses_75_review_gate_not_65_floor(self):
        # confidence=70 проходит порог min_score=65 архивного фонда, но
        # ниже отдельно указанного порога ручной верификации 75 — должен
        # уйти на ручную проверку, а не быть автоматически проиндексирован.
        doc = make_document(
            reg_number="OCR-THR-3", files_original="documents/originals/2026/01/thr3.pdf",
            ocr_category=NormativeDocument.OcrCategory.ARCHIVE,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence", return_value=("Текст", 70.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.NEEDS_REVIEW)

    def test_archive_at_75_is_indexed(self):
        doc = make_document(
            reg_number="OCR-THR-4", files_original="documents/originals/2026/01/thr4.pdf",
            ocr_category=NormativeDocument.OcrCategory.ARCHIVE,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence", return_value=("Текст", 75.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.INDEXED)

    def test_audit_details_include_threshold_and_status(self):
        doc = make_document(
            reg_number="OCR-THR-5", files_original="documents/originals/2026/01/thr5.pdf",
            ocr_category=NormativeDocument.OcrCategory.ARCHIVE,
        )
        with patch(
            "apps.documents.tasks.extract_text_and_confidence", return_value=("Текст", 70.0),
        ), _mock_storage_open():
            run_ocr_for_document.delay(str(doc.pk))

        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED, object_id=str(doc.pk),
        )
        self.assertEqual(entry.details["review_threshold"], 75)
        self.assertEqual(entry.details["ocr_status"], NormativeDocument.OcrStatus.NEEDS_REVIEW)


class RunOcrForDocumentFailureTests(TestCase):
    """self.request.retries задаётся напрямую через push_request()/run(), а не
    через .delay()/.apply() — у Celery в eager-режиме рекурсивный self-retry
    (apply() -> retval.sig.apply(retries+1)) всегда идёт по
    CELERY_TASK_EAGER_PROPAGATES приложения, игнорируя throw= конкретного
    вызова, так что явно проверить именно ГРАНИЦУ "retries == max_retries"
    одним detereminated вызовом через публичный API нельзя — push_request()
    эмулирует то же состояние, в котором реальный воркер вызывает задачу на
    N-й попытке."""

    def test_failure_before_max_retries_does_not_write_failed_audit(self):
        doc = make_document(reg_number="OCR-RETRY", files_original="documents/originals/2026/01/retry.pdf")

        run_ocr_for_document.push_request(retries=1)
        try:
            with patch(
                "apps.documents.tasks.extract_text_and_confidence",
                side_effect=RuntimeError("временная ошибка"),
            ), _mock_storage_open():
                with self.assertRaises(Exception):
                    run_ocr_for_document.run(str(doc.pk))
        finally:
            run_ocr_for_document.pop_request()

        self.assertFalse(
            AuditLog.objects.filter(object_id="OCR-RETRY").exists()
        )
        doc.refresh_from_db()
        self.assertEqual(doc.ocr_body, "")

    def test_writes_failed_audit_entry_after_exhausting_retries(self):
        doc = make_document(reg_number="OCR-FAIL", files_original="documents/originals/2026/01/fail.pdf")

        run_ocr_for_document.push_request(retries=run_ocr_for_document.max_retries)
        try:
            with patch(
                "apps.documents.tasks.extract_text_and_confidence",
                side_effect=RuntimeError("распознавание не удалось"),
            ), _mock_storage_open():
                run_ocr_for_document.run(str(doc.pk))
        finally:
            run_ocr_for_document.pop_request()

        entries = AuditLog.objects.filter(
            event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED, object_id=str(doc.pk),
        )
        self.assertEqual(entries.count(), 1)
        self.assertIn("распознавание не удалось", entries.first().details["error"])
        self.assertEqual(entries.first().details["retries"], run_ocr_for_document.max_retries)

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_body, "")
        self.assertIsNone(doc.ocr_confidence)

    def test_final_failure_sets_needs_review_status(self):
        doc = make_document(
            reg_number="OCR-FAIL-STATUS", files_original="documents/originals/2026/01/fail-status.pdf",
        )

        run_ocr_for_document.push_request(retries=run_ocr_for_document.max_retries)
        try:
            with patch(
                "apps.documents.tasks.extract_text_and_confidence",
                side_effect=RuntimeError("boom"),
            ), _mock_storage_open():
                run_ocr_for_document.run(str(doc.pk))
        finally:
            run_ocr_for_document.pop_request()

        doc.refresh_from_db()
        self.assertEqual(doc.ocr_status, NormativeDocument.OcrStatus.NEEDS_REVIEW)

    def test_no_completed_audit_entry_after_exhausting_retries(self):
        doc = make_document(reg_number="OCR-FAIL-2", files_original="documents/originals/2026/01/fail2.pdf")

        run_ocr_for_document.push_request(retries=run_ocr_for_document.max_retries)
        try:
            with patch(
                "apps.documents.tasks.extract_text_and_confidence",
                side_effect=RuntimeError("boom"),
            ), _mock_storage_open():
                run_ocr_for_document.run(str(doc.pk))
        finally:
            run_ocr_for_document.pop_request()

        self.assertFalse(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_OCR_COMPLETED, object_id=str(doc.pk),
            ).exists()
        )


class RunOcrForDocumentTimeLimitTests(TestCase):
    def test_time_limits_configured_for_large_scans(self):
        # ТЗ 1.2 §4.2.1: скан-оригинал до 150 МБ — жёсткий/мягкий таймаут
        # защищают воркер от одной зависшей задачи, а не только от медленной.
        self.assertEqual(run_ocr_for_document.time_limit, 600)
        self.assertEqual(run_ocr_for_document.soft_time_limit, 540)

    def test_soft_time_limit_exceeded_is_treated_like_other_failures(self):
        from celery.exceptions import SoftTimeLimitExceeded

        doc = make_document(
            reg_number="OCR-TIMEOUT", files_original="documents/originals/2026/01/timeout.pdf",
        )

        run_ocr_for_document.push_request(retries=run_ocr_for_document.max_retries)
        try:
            with patch(
                "apps.documents.tasks.extract_text_and_confidence",
                side_effect=SoftTimeLimitExceeded(),
            ), _mock_storage_open():
                run_ocr_for_document.run(str(doc.pk))
        finally:
            run_ocr_for_document.pop_request()

        self.assertTrue(
            AuditLog.objects.filter(
                event_type=AuditLog.EventType.DOCUMENT_OCR_FAILED, object_id=str(doc.pk),
            ).exists()
        )


class RunOcrForDocumentMissingDocumentTests(TestCase):
    def test_missing_document_is_a_silent_noop(self):
        run_ocr_for_document.delay(str(uuid.uuid4()))
        self.assertFalse(
            AuditLog.objects.filter(
                event_type__in=[
                    AuditLog.EventType.DOCUMENT_OCR_COMPLETED,
                    AuditLog.EventType.DOCUMENT_OCR_FAILED,
                ]
            ).exists()
        )
