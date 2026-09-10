"""Извлечение текста и уверенности распознавания из скана НРД (конвейер OCR,
Этап 3, README «Дальше по плану»).

Чистые функции без побочных эффектов (не знают о Django ORM/Celery/БД) —
вызываются из apps/documents/tasks.py, но тестируются напрямую, без брокера
и без реального документа в БД.
"""
from __future__ import annotations

import pytesseract
from django.conf import settings
from pdf2image import convert_from_bytes
from PIL import Image


def extract_text_and_confidence(pdf_bytes: bytes) -> tuple[str, float | None]:
    """Растеризует PDF постранично (pdf2image/poppler) и распознаёт текст
    каждой страницы (pytesseract/Tesseract, язык — settings.OCR_LANGUAGE).

    Возвращает (полный_текст, средняя_уверенность). Средняя уверенность —
    по словам, а не по страницам (word-count-weighted): простое среднее
    постраничных средних придало бы короткой странице такой же вес, как
    длинной. Tesseract отдаёт confidence уже в шкале 0-100 — она совпадает
    со шкалой MinValueValidator(0)/MaxValueValidator(100) на
    NormativeDocument.ocr_confidence не случайно, поле изначально заведено
    под эту шкалу.

    Строки уровня «блок/абзац/строка» (а не «слово») в image_to_data имеют
    пустой text и сигнальное conf=-1 — их достаточно отфильтровать по
    пустому тексту; отдельная проверка `conf >= 0` — защита на случай
    других сигнальных значений, а не признак того, что реальная
    (ненулевая) уверенность по распознанному слову тоже отбрасывается.
    """
    lang = getattr(settings, "OCR_LANGUAGE", "rus")
    pages: list[Image.Image] = convert_from_bytes(pdf_bytes, dpi=200)

    page_texts = []
    confidence_sum = 0.0
    confidence_count = 0

    for page in pages:
        page_texts.append(pytesseract.image_to_string(page, lang=lang))

        data = pytesseract.image_to_data(page, lang=lang, output_type=pytesseract.Output.DICT)
        for word, conf in zip(data["text"], data["conf"]):
            if not word.strip():
                continue
            conf_value = float(conf)
            if conf_value < 0:
                continue
            confidence_sum += conf_value
            confidence_count += 1

    full_text = "\n".join(page_texts)
    confidence = (confidence_sum / confidence_count) if confidence_count else None
    return full_text, confidence
