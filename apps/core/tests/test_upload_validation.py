import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from apps.core import upload_validation


class UploadSizeTests(SimpleTestCase):
    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_exact_limit_passes(self):
        upload_validation.validate_upload_size(
            SimpleUploadedFile("x.docx", b"x" * 10)
        )

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_one_byte_over_limit_rejects(self):
        with self.assertRaises(upload_validation.UploadTooLarge):
            upload_validation.validate_upload_size(
                SimpleUploadedFile("x.docx", b"x" * 11)
            )

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_seek_fallback_restores_position(self):
        stream = io.BytesIO(b"12345")
        field = mock.Mock(spec=["size", "file"])
        field.size = None
        field.file = stream
        upload_validation.validate_upload_size(field)
        self.assertEqual(stream.tell(), 0)

    @override_settings(UPLOAD_MAX_BYTES=10)
    def test_unknown_unseekable_size_rejects_fail_closed(self):
        field = mock.Mock(spec=["size", "file"])
        field.size = None
        field.file.seek.side_effect = OSError("not seekable")
        with self.assertRaises(upload_validation.UploadValidationError):
            upload_validation.validate_upload_size(field)
