"""Дифференцированные пороги качества OCR по категории документа (ТЗ 2.2
§4.4.2). `apps/documents/tasks.py` вызывает `get_threshold_for_document()`
после распознавания, чтобы выставить `NormativeDocument.ocr_status`
(`indexed`/`needs_review`).

ЧЕСТНЫЕ ГРАНИЦЫ:
- ТЗ также задаёт метрику Top-3 (доля документов, где правильное значение
  входит в тройку кандидатов) для каждой категории — она не реализована и
  не может быть посчитана на текущем пайплайне в принципе: pytesseract/
  Tesseract отдаёт одно распознанное слово на позицию, а не набор
  альтернативных кандидатов с вероятностями, измерять Top-3 нечем без
  смены движка распознавания.
- Для категории «Схемы и чертежи» ТЗ требует «Индексация штампа (ГОСТ
  2.104). Графика не индексируется» — не реализовано: вся страница
  распознаётся целиком (`apps/documents/ocr.py`), выделение области штампа
  потребовало бы отдельного шага детекции региона на странице, которого
  сейчас нет. Порог ниже для этой категории применяется ко всей странице.
"""
from __future__ import annotations

MODERN_NRD_FROM_YEAR = 2018


class OcrThreshold:
    """min_score — «Min OCR Score» из таблицы ТЗ 2.2 §4.4.2 (нижняя граница
    вообще приемлемого распознавания). review_below — порог, ниже которого
    документ обязательно уходит на ручную верификацию; для большинства
    категорий совпадает с min_score, но ТЗ явно называет для архивного
    фонда ОТДЕЛЬНОЕ число (75), отличное от его min_score (65) — это не
    опечатка таблицы, а два разных порога: 65 — нижняя граница вообще
    приемлемого результата, 75 — граница ручной проверки."""

    __slots__ = ("min_score", "review_below")

    def __init__(self, min_score: float, review_below: float | None = None):
        self.min_score = min_score
        self.review_below = min_score if review_below is None else review_below


OCR_THRESHOLDS = {
    "modern_nrd": OcrThreshold(min_score=90),
    "mixed_forms": OcrThreshold(min_score=75),
    "archive": OcrThreshold(min_score=65, review_below=75),
    "schemes": OcrThreshold(min_score=80),
}

DEFAULT_THRESHOLD = OcrThreshold(min_score=80)


def get_threshold_for_document(document) -> OcrThreshold:
    """document — apps.documents.models.NormativeDocument (без явной
    типизации — во избежание циклического импорта models<->этот модуль,
    оба импортируются вместе только из apps/documents/tasks.py)."""
    if document.ocr_category:
        return OCR_THRESHOLDS.get(document.ocr_category, DEFAULT_THRESHOLD)

    if document.reg_date:
        if document.reg_date.year >= MODERN_NRD_FROM_YEAR:
            return OCR_THRESHOLDS["modern_nrd"]
        return OCR_THRESHOLDS["archive"]

    return DEFAULT_THRESHOLD
