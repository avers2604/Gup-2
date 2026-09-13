import datetime
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core import upload_validation
from apps.documents.models import NormativeDocument
from apps.documents.retention import RetentionCategory
from apps.iam.models import Department


class DocumentUploadFormatTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(
            name="Format validation", level=Department.Level.SERVICE
        )

    def build(self, *, original=None, editable=None):
        return NormativeDocument(
            reg_number="FMT-1",
            reg_date=datetime.date(2026, 1, 1),
            effective_date=datetime.date(2026, 1, 2),
            doc_type=NormativeDocument.DocType.ORDER,
            title="Format validation",
            issuer_dept=self.dept,
            retention_category=RetentionCategory.ORDERS_CORE,
            files_original=original or "documents/originals/existing.pdf",
            files_editable=editable,
        )

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch(
        "apps.documents.models.upload_validation.validate_pdfa_2b",
        side_effect=upload_validation.PDFAValidationFailed("bad"),
    )
    def test_original_rejects_before_antivirus_and_staging(
        self, pdfa, antivirus, stage
    ):
        doc = self.build(
            original=SimpleUploadedFile("scan.pdf", b"ordinary pdf")
        )

        with self.assertRaises(upload_validation.PDFAValidationFailed):
            doc.save()

        antivirus.assert_not_called()
        stage.assert_not_called()
        self.assertFalse(
            NormativeDocument.objects.filter(reg_number="FMT-1").exists()
        )

    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch(
        "apps.documents.models.upload_validation.validate_ooxml",
        side_effect=upload_validation.InvalidOOXML("bad"),
    )
    def test_editable_rejects_before_antivirus_and_mutable_storage(
        self, ooxml, antivirus
    ):
        doc = self.build(
            editable=SimpleUploadedFile("draft.docx", b"bad")
        )

        with mock.patch.object(doc.files_editable.storage, "save") as storage_save:
            with self.assertRaises(upload_validation.InvalidOOXML):
                doc.save()

        antivirus.assert_not_called()
        storage_save.assert_not_called()

    @mock.patch.object(NormativeDocument, "_save_validated")
    @mock.patch("apps.documents.models.macro_check.reject_if_has_macros")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_ooxml")
    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_editable_order_is_size_format_antivirus_macro_save(
        self, size, ooxml, antivirus, macro, save
    ):
        events = []
        size.side_effect = lambda field: events.append("size")
        ooxml.side_effect = lambda field, kind: events.append("format")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        macro.side_effect = lambda *args, **kwargs: events.append("macro")
        save.side_effect = lambda *args, **kwargs: events.append("save")

        self.build(
            editable=SimpleUploadedFile("draft.docx", b"payload")
        ).save()

        self.assertEqual(
            events,
            ["size", "format", "antivirus", "macro", "save"],
        )

    @mock.patch.object(NormativeDocument, "_save_validated")
    @mock.patch("apps.documents.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.documents.models.upload_validation.validate_pdfa_2b")
    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_original_order_is_size_pdfa_antivirus_save(
        self, size, pdfa, antivirus, save
    ):
        events = []
        size.side_effect = lambda field: events.append("size")
        pdfa.side_effect = lambda field: events.append("pdfa")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        save.side_effect = lambda *args, **kwargs: events.append("save")

        self.build(
            original=SimpleUploadedFile("scan.pdf", b"payload")
        ).save()

        self.assertEqual(events, ["size", "pdfa", "antivirus", "save"])

    @mock.patch("apps.documents.models.upload_validation.validate_upload_size")
    def test_existing_committed_names_are_not_revalidated(self, size):
        doc = self.build()
        with mock.patch.object(NormativeDocument, "_save_validated"):
            doc.save()

        size.assert_not_called()
