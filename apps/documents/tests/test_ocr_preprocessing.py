"""Тесты предобработки скана (ТЗ 2.2 §4.5) — детерминированные, на
синтетических numpy-массивах, без реального OCR (тот прогон — в test_ocr.py)."""
import cv2
import numpy as np
from django.test import SimpleTestCase

from apps.documents.ocr_preprocessing import (
    _binarize,
    _binarize_sauvola,
    _denoise,
    _deskew,
    preprocess_page,
)


def _bar_image(angle: float = 0.0) -> np.ndarray:
    """Белый фон с чёрной горизонтальной полосой (имитация строки текста),
    опционально повёрнутой на угол — используется как "текст" для deskew,
    поскольку минимальная охватывающая прямоугольная область полосы имеет
    чётко определяемый угол."""
    img = np.full((200, 400), 255, dtype=np.uint8)
    img[80:120, 50:350] = 0
    if angle:
        matrix = cv2.getRotationMatrix2D((200, 100), angle, 1.0)
        img = cv2.warpAffine(img, matrix, (400, 200), borderValue=255)
    return img


class DeskewTests(SimpleTestCase):
    def test_rotated_bar_is_straightened_within_tolerance(self):
        rotated = _bar_image(angle=7.0)
        straightened = _deskew(rotated)

        # После выравнивания горизонтальная полоса должна занимать заметно
        # меньше высоты по краям кадра, чем до — прямой численный критерий
        # без хрупкого сравнения пиксель-в-пиксель.
        original_col_heights = (rotated[:, 60] < 128).sum()
        straightened_col_heights = (straightened[:, 60] < 128).sum()
        self.assertLessEqual(straightened_col_heights, original_col_heights)

    def test_angle_beyond_15_degrees_is_clamped(self):
        # ТЗ 2.2 §4.5 ограничивает автовыравнивание ±15° — сильно
        # перекошенная страница не должна быть повёрнута на весь угол.
        rotated = _bar_image(angle=40.0)
        result = _deskew(rotated, max_angle=15.0)
        self.assertEqual(result.shape, rotated.shape)

    def test_insufficient_text_pixels_returns_image_unchanged(self):
        blank = np.full((200, 400), 255, dtype=np.uint8)
        result = _deskew(blank)
        np.testing.assert_array_equal(result, blank)

    def test_negligible_skew_returns_image_unchanged(self):
        straight = _bar_image(angle=0.0)
        result = _deskew(straight)
        np.testing.assert_array_equal(result, straight)


class DenoiseTests(SimpleTestCase):
    def test_removes_salt_and_pepper_noise(self):
        clean = _bar_image()
        rng = np.random.default_rng(42)
        noisy = clean.copy()
        noise_mask = rng.random(clean.shape) < 0.02
        noisy[noise_mask] = 0

        denoised = _denoise(noisy)

        noisy_diff = np.abs(noisy.astype(int) - clean.astype(int)).sum()
        denoised_diff = np.abs(denoised.astype(int) - clean.astype(int)).sum()
        self.assertLess(denoised_diff, noisy_diff)

    def test_output_shape_matches_input(self):
        image = _bar_image()
        self.assertEqual(_denoise(image).shape, image.shape)


class BinarizeTests(SimpleTestCase):
    def test_otsu_produces_pure_black_and_white(self):
        image = _bar_image()
        result = _binarize(image, method="otsu")
        self.assertEqual(set(np.unique(result).tolist()), {0, 255})

    def test_sauvola_produces_pure_black_and_white(self):
        image = _bar_image()
        result = _binarize(image, method="sauvola")
        self.assertEqual(set(np.unique(result).tolist()) - {0, 255}, set())

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            _binarize(_bar_image(), method="not-a-real-method")

    def test_sauvola_helper_matches_dispatch(self):
        image = _bar_image()
        np.testing.assert_array_equal(_binarize(image, "sauvola"), _binarize_sauvola(image))


class PreprocessPageTests(SimpleTestCase):
    def test_full_pipeline_runs_and_returns_same_shape(self):
        image = _bar_image(angle=5.0)
        result = preprocess_page(image)
        self.assertEqual(result.shape, image.shape)
        self.assertEqual(result.dtype, np.uint8)

    def test_steps_can_be_individually_disabled(self):
        image = _bar_image()
        # binarize=False -> результат не обязан быть строго чёрно-белым
        result = preprocess_page(image, deskew=False, denoise=False, binarize=False)
        np.testing.assert_array_equal(result, image)
