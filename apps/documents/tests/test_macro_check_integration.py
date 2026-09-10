"""Структурная проверка на макросы (ТЗ 4.7) на загрузке карточки НРД —
NormativeDocument.save(), apps/core/macro_check.py. Файл проходит ClamAV
(живой clamd, apps/core/tests/clamd_fixture.py — макросы сами по себе не
детектируются антивирусом по сигнатуре), затем блокируется структурной
проверкой."""
import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.core.macro_check import MacrosDetected
from apps.core.tests.clamd_fixture import ClamdTestCase

from ..models import NormativeDocument
from .factories import make_document


def _docx_bytes(with_macro: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
        if with_macro:
            zf.writestr("word/vbaProject.bin", b"fake vba bytecode")
    return buf.getvalue()


class NormativeDocumentMacroCheckTests(ClamdTestCase):
    def test_docx_without_macros_saves_fine(self):
        clean_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean", content_type="application/pdf")
        clean_docx = SimpleUploadedFile("draft.docx", _docx_bytes(with_macro=False))
        doc = make_document(reg_number="MC-1", files_original=clean_pdf, files_editable=clean_docx)
        doc.refresh_from_db()
        self.assertTrue(doc.files_editable.name)

    def test_docx_with_macro_blocks_save(self):
        clean_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 clean", content_type="application/pdf")
        infected_docx = SimpleUploadedFile("draft.docx", _docx_bytes(with_macro=True))
        with self.assertRaises(MacrosDetected):
            make_document(reg_number="MC-2", files_original=clean_pdf, files_editable=infected_docx)
        self.assertFalse(NormativeDocument.objects.filter(reg_number="MC-2").exists())

    def test_macro_rejection_writes_audit_entry(self):
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

    def test_pdf_with_zip_like_extension_confusion_is_not_falsely_flagged(self):
        # files_original — настоящий PDF (не ZIP) даже если бы кто-то
        # переименовал файл: contains_macros() на не-ZIP просто пусто.
        real_pdf = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 genuinely a pdf", content_type="application/pdf")
        doc = make_document(reg_number="MC-4", files_original=real_pdf)
        doc.refresh_from_db()
        self.assertTrue(doc.files_original.name)
