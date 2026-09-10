import datetime
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.documents.ocr_thresholds import (
    DEFAULT_THRESHOLD,
    OCR_THRESHOLDS,
    get_threshold_for_document,
)


def _doc(ocr_category="", reg_date=None):
    return SimpleNamespace(ocr_category=ocr_category, reg_date=reg_date)


class GetThresholdForDocumentTests(SimpleTestCase):
    def test_explicit_category_wins_over_date(self):
        doc = _doc(ocr_category="archive", reg_date=datetime.date(2026, 1, 1))
        self.assertIs(get_threshold_for_document(doc), OCR_THRESHOLDS["archive"])

    def test_unknown_explicit_category_falls_back_to_default(self):
        doc = _doc(ocr_category="not-a-real-category", reg_date=datetime.date(2026, 1, 1))
        self.assertIs(get_threshold_for_document(doc), DEFAULT_THRESHOLD)

    def test_modern_date_without_category_infers_modern_nrd(self):
        doc = _doc(reg_date=datetime.date(2018, 1, 1))
        self.assertIs(get_threshold_for_document(doc), OCR_THRESHOLDS["modern_nrd"])

    def test_pre_2018_date_without_category_infers_archive(self):
        doc = _doc(reg_date=datetime.date(2017, 12, 31))
        self.assertIs(get_threshold_for_document(doc), OCR_THRESHOLDS["archive"])

    def test_no_category_and_no_date_uses_default(self):
        doc = _doc(reg_date=None)
        self.assertIs(get_threshold_for_document(doc), DEFAULT_THRESHOLD)


class ThresholdValuesTests(SimpleTestCase):
    def test_modern_nrd_matches_tz_table(self):
        self.assertEqual(OCR_THRESHOLDS["modern_nrd"].min_score, 90)
        self.assertEqual(OCR_THRESHOLDS["modern_nrd"].review_below, 90)

    def test_mixed_forms_matches_tz_table(self):
        self.assertEqual(OCR_THRESHOLDS["mixed_forms"].min_score, 75)

    def test_schemes_matches_tz_table(self):
        self.assertEqual(OCR_THRESHOLDS["schemes"].min_score, 80)

    def test_archive_has_distinct_min_score_and_review_threshold(self):
        # ТЗ 2.2 §4.4.2 называет для архива ДВА разных числа: 65 (нижняя
        # граница вообще приемлемого распознавания) и 75 (отдельно указанная
        # граница обязательной ручной верификации) — это не опечатка,
        # значения намеренно различаются.
        archive = OCR_THRESHOLDS["archive"]
        self.assertEqual(archive.min_score, 65)
        self.assertEqual(archive.review_below, 75)
        self.assertNotEqual(archive.min_score, archive.review_below)

    def test_review_below_defaults_to_min_score_when_not_given(self):
        from apps.documents.ocr_thresholds import OcrThreshold

        threshold = OcrThreshold(min_score=42)
        self.assertEqual(threshold.review_below, 42)
