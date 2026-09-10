"""Извлечение текста и уверенности распознавания из скана НРД (конвейер OCR,
Этап 3, README «Дальше по плану»).

Чистые функции без побочных эффектов (не знают о Django ORM/Celery/БД) —
вызываются из apps/documents/tasks.py, но тестируются напрямую, без брокера
и без реального документа в БД.
"""
from __future__ import annotations

import numpy as np
import pytesseract
from django.conf import settings
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from PIL import Image

from .ocr_preprocessing import preprocess_page

DPI = 200
# Страницы обрабатываются пакетами, а не все сразу — при лимите ТЗ 1.2
# §4.2.1 на скан-оригинал (150 МБ, потенциально сотни страниц)
# единовременная растеризация всего файла держала бы в памяти сразу все
# страницы; пакет ограничивает пик памяти его размером независимо от
# общего числа страниц документа.
DEFAULT_BATCH_SIZE = 10


def extract_text_and_confidence(
    pdf_bytes: bytes,
    *,
    preprocess: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[str, float | None]:
    """Растеризует PDF постранично, пакетами по `batch_size` страниц
    (pdf2image/poppler), и распознаёт текст каждой страницы
    (pytesseract/Tesseract, язык — settings.OCR_LANGUAGE).

    `preprocess=True` (по умолчанию) прогоняет каждую страницу через
    apps.documents.ocr_preprocessing.preprocess_page — автовыравнивание
    перекоса, подавление шума/перфорации, адаптивная бинаризация (ТЗ 2.2
    §4.5). Без неё метрики качества ТЗ 2.2 §4.4.2 (apps/documents/ocr_thresholds.py),
    особенно пониженный порог архивного фонда, недостижимы на
    пожелтевших/перекошенных сканах — параметр оставлен только для тестов,
    сравнивающих поведение с/без предобработки.

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
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size must be in 1..10")
    if len(pdf_bytes) > settings.OCR_MAX_BYTES:
        raise ValueError("PDF exceeds OCR byte limit")
    timeout = settings.OCR_PROCESS_TIMEOUT
    total_pages = pdfinfo_from_bytes(pdf_bytes, timeout=timeout)["Pages"]
    if not 1 <= total_pages <= settings.OCR_MAX_PAGES:
        raise ValueError("PDF exceeds OCR page limit")

    page_texts = []
    confidence_sum = 0.0
    confidence_count = 0

    for batch_start in range(1, total_pages + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, total_pages)
        pages: list[Image.Image] = convert_from_bytes(
            pdf_bytes, dpi=DPI, first_page=batch_start, last_page=batch_end,
            timeout=timeout, size=settings.OCR_MAX_DIMENSION, thread_count=1,
        )

        for page in pages:
            if preprocess:
                grayscale = np.array(page.convert("L"))
                page = Image.fromarray(preprocess_page(grayscale))

            data = pytesseract.image_to_data(page, lang=lang, timeout=timeout,
                                            output_type=pytesseract.Output.DICT)
            lines = {}
            for i, word in enumerate(data["text"]):
                if word.strip():
                    key = tuple(data[k][i] for k in ("block_num", "par_num", "line_num"))
                    lines.setdefault(key, []).append(word)
            page_texts.append("\n".join(" ".join(words) for words in lines.values()))
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
