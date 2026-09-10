"""Предобработка растрового скана перед OCR (ТЗ 2.2 §4.5) — автовыравнивание
перекоса, подавление шума/перфорации, адаптивная бинаризация. Нужна для
метрик качества распознавания ТЗ 2.2 §4.4.2 (`apps/documents/ocr_thresholds.py`):
без неё пожелтевший/перекошенный архивный скан не дотягивает даже до
пониженного порога архивного фонда.

Чистые функции над numpy-массивом (не знают о PIL/Django) — вызываются из
apps/documents/ocr.py, тестируются напрямую."""
from __future__ import annotations

import cv2
import numpy as np

DESKEW_MAX_ANGLE = 15.0
DESKEW_MIN_TEXT_PIXELS = 100
DESKEW_MIN_ANGLE = 0.5


def preprocess_page(
    image: np.ndarray,
    *,
    deskew: bool = True,
    denoise: bool = True,
    binarize: bool = True,
    binarize_method: str = "otsu",
) -> np.ndarray:
    """image — grayscale (одноканальный) uint8-массив страницы."""
    if deskew:
        image = _deskew(image)
    if denoise:
        image = _denoise(image)
    if binarize:
        image = _binarize(image, method=binarize_method)
    return image


def _deskew(image: np.ndarray, max_angle: float = DESKEW_MAX_ANGLE) -> np.ndarray:
    """Автовыравнивание перекоса, ограничено ±15° (ТЗ 2.2 §4.5)."""
    inverted = 255 - image
    coords = np.column_stack(np.where(inverted > 0))

    if len(coords) < DESKEW_MIN_TEXT_PIXELS:
        return image  # слишком мало текста, чтобы надёжно определить угол

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    angle = max(-max_angle, min(max_angle, angle))

    if abs(angle) < DESKEW_MIN_ANGLE:
        return image  # перекос незначительный, поворот только добавил бы шум

    height, width = image.shape[:2]
    center = (width // 2, height // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, rotation_matrix, (width, height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def _denoise(image: np.ndarray) -> np.ndarray:
    """Подавление шума скана и артефактов перфорации архивных дел.

    Текст скана — тёмный на светлом фоне страницы, перфорация/пыль на
    сканере физически проявляются как мелкие ТЁМНЫЕ точки на этом светлом
    фоне (не наоборот). Убрать именно такие мелкие тёмные точки, не тронув
    штрихи обычного шрифта, — задача морфологического ЗАКРЫТИЯ (сначала
    дилатация, потом эрозия): оно стирает мелкие тёмные вкрапления меньше
    ядра. Морфологическое ОТКРЫТИЕ делает обратное — убирает мелкие
    СВЕТЛЫЕ пятна на тёмном фоне, на этой полярности изображения оно
    только увеличивает тёмный шум, а не убирает его (проверено на
    синтетическом тесте, см. apps/documents/tests/test_ocr_preprocessing.py)."""
    denoised = cv2.fastNlMeansDenoising(image, None, h=10, templateWindowSize=7, searchWindowSize=21)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, kernel)


def _binarize(image: np.ndarray, method: str = "otsu") -> np.ndarray:
    """Адаптивная бинаризация — Otsu (по умолчанию) или Sauvola (ТЗ 2.2 §4.5
    называет оба метода, без указания, когда какой; Otsu взят как метод по
    умолчанию — дешевле и устойчивее на однородном фоне современных НРД,
    Sauvola доступен явным выбором для неоднородного фона архивных сканов)."""
    if method == "otsu":
        return cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    if method == "sauvola":
        return _binarize_sauvola(image)
    raise ValueError(f"Неизвестный метод бинаризации: {method!r} (ожидается 'otsu' или 'sauvola')")


def _binarize_sauvola(image: np.ndarray, window_size: int = 25, k: float = 0.08) -> np.ndarray:
    image_f = image.astype(np.float32)
    mean = cv2.blur(image_f, (window_size, window_size))
    sqmean = cv2.blur(image_f ** 2, (window_size, window_size))
    std = np.sqrt(np.maximum(sqmean - mean ** 2, 0))
    threshold = mean * (1 + k * (std / 128.0 - 1))
    return ((image > threshold) * 255).astype(np.uint8)
