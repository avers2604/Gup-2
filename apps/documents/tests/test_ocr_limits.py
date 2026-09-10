from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from apps.documents.ocr import extract_text_and_confidence


class OcrLimitsTests(SimpleTestCase):
    @override_settings(OCR_MAX_BYTES=2)
    def test_oversize_input_is_rejected_before_poppler(self):
        with patch("apps.documents.ocr.pdfinfo_from_bytes") as info:
            with self.assertRaises(ValueError):
                extract_text_and_confidence(b"large")
        info.assert_not_called()

    @override_settings(OCR_MAX_PAGES=10)
    def test_page_limit_prevents_rasterization(self):
        with patch("apps.documents.ocr.pdfinfo_from_bytes", return_value={"Pages": 11}), patch("apps.documents.ocr.convert_from_bytes") as convert:
            with self.assertRaises(ValueError):
                extract_text_and_confidence(b"pdf")
        convert.assert_not_called()

    def test_invalid_batch_is_rejected(self):
        for value in (0, -1, 11):
            with self.assertRaises(ValueError):
                extract_text_and_confidence(b"pdf", batch_size=value)
