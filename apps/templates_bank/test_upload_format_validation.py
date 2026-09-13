from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.core import upload_validation
from apps.documents.tests.factories import make_document
from apps.templates_bank.models import Template, TemplateFamily


class TemplateUploadFormatTests(TestCase):
    def setUp(self):
        self.family = TemplateFamily.objects.create(name="Format validation")
        self.approving = make_document(reg_number="FMT-TEMPLATE")

    def build(self, *, editable=None, sample=None):
        return Template(
            family=self.family,
            version="v1.0",
            change_type=Template.ChangeType.MAJOR,
            approving_document=self.approving,
            file_editable=editable or "templates/editable/existing.docx",
            file_sample=sample or "templates/samples/existing.pdf",
        )

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch(
        "apps.templates_bank.models.upload_validation.validate_ooxml",
        side_effect=upload_validation.InvalidOOXML("bad"),
    )
    def test_editable_rejects_before_antivirus_and_staging(
        self, ooxml, antivirus, stage
    ):
        template = self.build(
            editable=SimpleUploadedFile("form.docx", b"bad")
        )

        with self.assertRaises(upload_validation.InvalidOOXML):
            template.save()

        antivirus.assert_not_called()
        stage.assert_not_called()

    @mock.patch("apps.core.staged_files.stage_uploaded_field")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch(
        "apps.templates_bank.models.upload_validation.validate_pdf",
        side_effect=upload_validation.InvalidPDF("bad"),
    )
    def test_sample_rejects_before_antivirus_and_staging(
        self, pdf, antivirus, stage
    ):
        template = self.build(
            sample=SimpleUploadedFile("sample.pdf", b"bad")
        )

        with self.assertRaises(upload_validation.InvalidPDF):
            template.save()

        antivirus.assert_not_called()
        stage.assert_not_called()

    @mock.patch("apps.templates_bank.models.macro_check.reject_if_has_macros")
    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_ooxml")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    @mock.patch("django.db.models.Model.save")
    def test_editable_order_is_size_format_antivirus_macro_save(
        self, base_save, size, ooxml, antivirus, macro
    ):
        events = []
        size.side_effect = lambda field: events.append("size")
        ooxml.side_effect = lambda field, kind: events.append("format")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        macro.side_effect = lambda *args, **kwargs: events.append("macro")
        base_save.side_effect = lambda *args, **kwargs: events.append("save")

        self.build(
            editable=SimpleUploadedFile("form.docx", b"payload")
        ).save()

        self.assertEqual(
            events[:5],
            ["size", "format", "antivirus", "macro", "save"],
        )

    @mock.patch("apps.templates_bank.models.antivirus.scan_uploaded_field")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_pdf")
    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    @mock.patch("django.db.models.Model.save")
    def test_sample_order_is_size_pdf_antivirus_save(
        self, base_save, size, pdf, antivirus
    ):
        events = []
        size.side_effect = lambda field: events.append("size")
        pdf.side_effect = lambda field: events.append("pdf")
        antivirus.side_effect = lambda *args, **kwargs: events.append("antivirus")
        base_save.side_effect = lambda *args, **kwargs: events.append("save")

        self.build(
            sample=SimpleUploadedFile("sample.pdf", b"payload")
        ).save()

        self.assertEqual(events[:4], ["size", "pdf", "antivirus", "save"])

    @mock.patch("apps.templates_bank.models.upload_validation.validate_upload_size")
    def test_existing_committed_names_are_not_revalidated(self, size):
        with mock.patch("django.db.models.Model.save"):
            self.build().save()

        size.assert_not_called()
