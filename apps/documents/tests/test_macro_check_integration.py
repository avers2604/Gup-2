"""Структурная проверка на макросы (ТЗ 4.7) на загрузке карточки НРД —
NormativeDocument.save(), apps/core/macro_check.py. Файл проходит ClamAV
(живой clamd, apps/core/tests/clamd_fixture.py — макросы сами по себе не
детектируются антивирусом по сигнатуре), затем блокируется структурной
проверкой."""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditLog
from apps.core.macro_check import MacrosDetected
from apps.core.tests.clamd_fixture import ClamdTestCase
from apps.core.tests.upload_fixtures import docx_bytes

from ..models import NormativeDocument
from .factories import make_document


def _docx_bytes(with_macro: bool) -> bytes:
    extra = (("word/vbaProject.bin", b"fake vba bytecode"),) if with_macro else ()
    return docx_bytes(extra=extra)


class NormativeDocumentMacroCheckTests(ClamdTestCase):
    @patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    def test_docx_without_macros_saves_fine(self, _pdfa):
        clean_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean", content_type="application/pdf")
        clean_docx = SimpleUploadedFile("draft.docx", _docx_bytes(with_macro=False))
        doc = make_document(reg_number="MC-1", files_original=clean_pdf, files_editable=clean_docx)
        doc.refresh_from_db()
        self.assertTrue(doc.files_editable.name)

    @patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    def test_docx_with_macro_blocks_save(self, _pdfa):
        clean_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean", content_type="application/pdf")
        infected_docx = SimpleUploadedFile("draft.docx", _docx_bytes(with_macro=True))
        with self.assertRaises(MacrosDetected):
            make_document(reg_number="MC-2", files_original=clean_pdf, files_editable=infected_docx)
        self.assertFalse(NormativeDocument.objects.filter(reg_number="MC-2").exists())

    @patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    def test_macro_rejection_writes_audit_entry(self, _pdfa):
        clean_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean", content_type="application/pdf")
        infected_docx = SimpleUploadedFile("draft.docx", _docx_bytes(with_macro=True))
        with self.assertRaises(MacrosDetected):
            make_document(reg_number="MC-3", files_original=clean_pdf, files_editable=infected_docx)
        entry = AuditLog.objects.get(
            event_type=AuditLog.EventType.UPLOAD_MACRO_REJECTED,
        )
        self.assertEqual(entry.details["object_label"], "MC-3")
        self.assertEqual(entry.details["field"], "files_editable")
        self.assertIn("word/vbaProject.bin", entry.details["markers"])

    @patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    def test_pdf_with_zip_like_extension_confusion_is_not_falsely_flagged(self, _pdfa):
        # files_original — PDF, поэтому macro-check к нему не применяется.
        real_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 genuinely a pdf", content_type="application/pdf")
        doc = make_document(reg_number="MC-4", files_original=real_pdf)
        doc.refresh_from_db()
        self.assertTrue(doc.files_original.name)
