from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation
from apps.core.tests.upload_fixtures import xlsx_bytes
from apps.iam.async_import import stage_personnel_import


class PersonnelImportIntakeValidationTests(SimpleTestCase):
    @mock.patch("apps.iam.async_import.working_storage")
    def test_invalid_xlsx_is_rejected_before_staging(self, working_storage):
        storage = working_storage.return_value
        upload = SimpleUploadedFile("personnel.xlsx", b"not-a-zip")

        with self.assertRaises(upload_validation.InvalidOOXML):
            stage_personnel_import(upload, original_name=upload.name)

        storage.save.assert_not_called()

    @override_settings(UPLOAD_MAX_BYTES=3)
    @mock.patch("apps.iam.async_import.working_storage")
    def test_oversized_import_is_rejected_before_staging(self, working_storage):
        storage = working_storage.return_value
        upload = SimpleUploadedFile("personnel.xlsx", b"1234")

        with self.assertRaises(upload_validation.UploadTooLarge):
            stage_personnel_import(upload, original_name=upload.name)

        storage.save.assert_not_called()

    @mock.patch("apps.iam.async_import.working_storage")
    @mock.patch("apps.core.antivirus.scan_file")
    @mock.patch("apps.core.upload_validation.validate_ooxml")
    @mock.patch("apps.core.upload_validation.validate_upload_size")
    def test_validation_order_is_size_ooxml_antivirus_then_storage(
        self,
        validate_size,
        validate_ooxml,
        scan_file,
        working_storage,
    ):
        events = []
        validate_size.side_effect = lambda file: events.append("size")
        validate_ooxml.side_effect = lambda file, kind: events.append(f"format:{kind}")
        scan_file.side_effect = lambda file: events.append("antivirus")
        storage = working_storage.return_value
        storage.save.side_effect = lambda name, file: events.append("storage") or name
        upload = SimpleUploadedFile("personnel.xlsx", xlsx_bytes())

        stage_personnel_import(upload, original_name=upload.name)

        self.assertEqual(events, ["size", "format:xlsx", "antivirus", "storage"])
