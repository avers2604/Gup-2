"""Состояния таблицы: подсветка строки, край прокрутки, плотный режим.

До этого у строки не было ни одного состояния. Реестр и журнал показывают
по 25–50 строк, а `min-width: 560px` гарантирует горизонтальную прокрутку
на ноутбуке: глаз терял строку на полпути и читал реквизит от соседнего
документа. Тест сторожит правила стиля, а не пиксели — проверяет, что
соответствующие объявления существуют и что решения не отменены молча.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS = (Path(settings.BASE_DIR) / "static" / "css" / "components.css").read_text(encoding="utf-8")
TOKENS = (Path(settings.BASE_DIR) / "static" / "css" / "tokens.css").read_text(encoding="utf-8")


class TableStateTests(SimpleTestCase):
    def test_row_is_highlighted_under_the_cursor(self):
        self.assertIn(".table tbody tr:hover", CSS)

    def test_row_is_highlighted_when_it_holds_keyboard_focus(self):
        """Клавиатура ведёт по ссылкам внутри строки, а не по строкам."""
        self.assertIn(".table tbody tr:focus-within", CSS)

    def test_scroll_edge_shadow_uses_a_token_not_a_literal_colour(self):
        """В тёмной теме тень обязана быть темнее фона, а не светлее."""
        self.assertIn("var(--shadow-scroll-edge)", CSS)
        self.assertNotIn("rgba(27, 33, 64, .14)", CSS)

    def test_scroll_edge_token_is_defined_for_both_themes(self):
        """Один на светлую и два на тёмную ветку (media + data-theme)."""
        self.assertEqual(TOKENS.count("--shadow-scroll-edge:"), 3)

    def test_sticky_header_is_absent_deliberately_and_the_reason_is_recorded(self):
        """Липкая шапка требует ограничить высоту — это вторая полоса прокрутки.

        Решение легко отменить по невнимательности, поэтому проверяется и
        отсутствие свойства, и наличие объяснения рядом.
        """
        self.assertNotIn("position: sticky", CSS)
        self.assertIn("Липкой шапки здесь НЕТ намеренно", CSS)

    def test_dense_mode_exists_for_long_narrow_tables(self):
        self.assertIn(".table--dense", CSS)

    def test_row_transition_respects_reduced_motion(self):
        """Анимация фона — украшение; тому, кто её отключил, она не нужна."""
        self.assertIn("prefers-reduced-motion: no-preference", CSS)
