"""Антивирусная проверка (ТЗ 4.7) на загрузке карточки НРД —
NormativeDocument.save(), apps/core/antivirus.py. Живой clamd
(apps/core/tests/clamd_fixture.py, EICAR-тестовая сигнатура), не
замоканный: тот же принцип, что и test_ocr.py для Tesseract."""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.core.antivirus import AntivirusUnavailable, MalwareDetected
from apps.core.tests.clamd_fixture import EICAR_BYTES, ClamdTestCase

from ..models import NormativeDocument
from .factories import make_document


class NormativeDocumentAntivirusTests(ClamdTestCase):
    def test_clean_file_upload_succeeds(self):
        upload = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean content", content_type="application/pdf")
        doc = make_document(reg_number="AV-1", files_original=upload)
        doc.refresh_from_db()
        self.assertTrue(doc.files_original.name)

    def test_eicar_upload_blocks_save(self):
        upload = SimpleUploadedFile("scan.pdf", EICAR_BYTES, content_type="application/pdf")
        with self.assertRaises(MalwareDetected):
            make_document(reg_number="AV-2", files_original=upload)
        self.assertFalse(NormativeDocument.objects.filter(reg_number="AV-2").exists())

    def test_eicar_upload_writes_audit_entry(self):
        upload = SimpleUploadedFile("scan.pdf", EICAR_BYTES, content_type="application/pdf")
        with self.assertRaises(MalwareDetected):
            make_document(reg_number="AV-3", files_original=upload)
        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.UPLOAD_MALWARE_DETECTED, object_id="AV-3",
        )
        self.assertEqual(entry.details["field"], "files_original")
        self.assertIn("Eicar-Test-Signature", entry.details["signature"])

    def test_eicar_in_editable_file_also_blocks_save(self):
        clean = SimpleUploadedFile("scan.pdf", b"clean content", content_type="application/pdf")
        infected = SimpleUploadedFile("draft.docx", EICAR_BYTES)
        with self.assertRaises(MalwareDetected):
            make_document(reg_number="AV-4", files_original=clean, files_editable=infected)

    def test_resaving_without_new_file_does_not_rescan(self):
        upload = SimpleUploadedFile("scan.pdf", b"clean content", content_type="application/pdf")
        doc = make_document(reg_number="AV-5", files_original=upload)

        with patch("apps.core.antivirus.scan_file") as mock_scan:
            doc.title = "Обновлённый заголовок"
            doc.save()
        mock_scan.assert_not_called()

    def test_string_only_files_original_is_not_scanned(self):
        # Тот же случай, что во всех остальных тестах проекта — имя файла
        # присвоено строкой (без реальной загрузки), сканировать нечего.
        with patch("apps.core.antivirus.scan_file") as mock_scan:
            make_document(reg_number="AV-6", files_original="documents/originals/2026/01/av6.pdf")
        mock_scan.assert_not_called()


class NormativeDocumentAntivirusUnavailableTests(ClamdTestCase):
    def test_clamd_unavailable_blocks_save(self):
        upload = SimpleUploadedFile("scan.pdf", b"clean content", content_type="application/pdf")
        with override_settings(CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1):
            with self.assertRaises(AntivirusUnavailable):
                make_document(reg_number="AV-7", files_original=upload)
        self.assertFalse(NormativeDocument.objects.filter(reg_number="AV-7").exists())
