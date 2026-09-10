"""Реальные (не замоканные) тесты извлечения текста/уверенности — Tesseract
и poppler действительно вызываются. Используют системные пакеты
(tesseract-ocr, tesseract-ocr-rus, poppler-utils, fonts-dejavu-core),
устанавливаемые в CI (.github/workflows/ci.yml)."""
import io

from django.test import SimpleTestCase
from PIL import Image, ImageDraw, ImageFont

from apps.documents.ocr import extract_text_and_confidence

DEJAVU_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _pdf_bytes_with_text(text):
    font = ImageFont.truetype(DEJAVU_SANS, 28)
    img = Image.new("RGB", (900, 150), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 50), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PDF")
    return buf.getvalue()


def _blank_pdf_bytes():
    img = Image.new("RGB", (300, 150), "white")
    buf = io.BytesIO()
    img.save(buf, "PDF")
    return buf.getvalue()


def _page_image(text):
    font = ImageFont.truetype(DEJAVU_SANS, 28)
    img = Image.new("RGB", (900, 150), "white")
    ImageDraw.Draw(img).text((20, 50), text, fill="black", font=font)
    return img


class ExtractTextAndConfidenceTests(SimpleTestCase):
    def test_recognizes_russian_text(self):
        text, confidence = extract_text_and_confidence(
            _pdf_bytes_with_text("Приказ № 142 от 01.01.2026")
        )
        self.assertIn("142", text)
        self.assertIn("Приказ", text)

    def test_confidence_is_on_0_100_scale(self):
        _text, confidence = extract_text_and_confidence(
            _pdf_bytes_with_text("Приказ № 142 от 01.01.2026")
        )
        self.assertIsNotNone(confidence)
        self.assertGreaterEqual(confidence, 0)
        self.assertLessEqual(confidence, 100)

    def test_blank_page_yields_no_confidence(self):
        text, confidence = extract_text_and_confidence(_blank_pdf_bytes())
        self.assertEqual(text.strip(), "")
        self.assertIsNone(confidence)

    def test_multi_page_pdf_concatenates_text_from_all_pages(self):
        page1 = _page_image("Приказ № 142")
        page2 = _page_image("Страница два")
        buf = io.BytesIO()
        page1.save(buf, "PDF", save_all=True, append_images=[page2])
        text, confidence = extract_text_and_confidence(buf.getvalue())
        self.assertIn("142", text)
        self.assertIn("два", text)
        self.assertIsNotNone(confidence)
