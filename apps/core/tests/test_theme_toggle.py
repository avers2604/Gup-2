"""Переключатель темы.

Токены [data-theme="dark"] были объявлены с самого веб-порта, но атрибут
никто не ставил: работала только системная настройка, а половина
написанного кода темы была недостижима.
"""
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase

from apps.documents.tests.test_permissions import PASSWORD, make_user

BASE_TEMPLATE = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")
THEME_JS = (Path(settings.BASE_DIR) / "static" / "js" / "theme.js").read_text(encoding="utf-8")


class ThemeToggleTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="1100")
        self.client.login(personnel_number="1100", password=PASSWORD)

    def test_toggle_is_present_on_every_page(self):
        body = self.client.get("/documents/").content.decode()

        self.assertIn('id="theme-toggle"', body)

    def test_toggle_is_a_button_not_a_link(self):
        """Переключение темы ничего не открывает и никуда не ведёт."""
        self.assertIn('<button id="theme-toggle"', BASE_TEMPLATE)

    def test_toggle_has_a_text_label_for_screen_readers(self):
        """Видимый текст — пиктограмма, сама по себе она ничего не значит."""
        self.assertIn('aria-label="Тема оформления"', BASE_TEMPLATE)
        self.assertIn("aria-label", THEME_JS)

    def test_theme_is_applied_before_first_paint(self):
        """Иначе при тёмной теме видна светлая вспышка.

        Встроенный скрипт обязан стоять ДО внешних: внешний файл
        грузится асинхронно от отрисовки, и гарантии порядка нет.
        """
        inline = BASE_TEMPLATE.index("bz-get-theme")
        external = BASE_TEMPLATE.index("js/theme.js")

        self.assertLess(inline, external)
        self.assertLess(inline, BASE_TEMPLATE.index("<body>"))

    def test_three_states_not_two(self):
        """«Как в системе» — не то же самое, что «светлая».

        Переключивший ОС на ночной режим ждёт, что приложение последует за
        ней; без третьего состояния сбросить свой выбор нечем.
        """
        self.assertIn('["system", "light", "dark"]', THEME_JS)

    def test_system_state_removes_the_attribute_rather_than_setting_light(self):
        """Атрибут должен именно сниматься, иначе prefers-color-scheme не сработает."""
        self.assertIn('root.removeAttribute("data-theme")', THEME_JS)

    def test_unavailable_storage_does_not_break_the_toggle(self):
        """Приватный режим: тема не запоминается, но переключаться обязана.

        Обращений к хранилищу три — чтение при старте, чтение до первой
        отрисовки и запись при переключении, — и каждое обязано быть
        защищено: в приватном режиме localStorage бросает исключение уже
        на доступе к свойству, а не на записи.
        """
        guarded = THEME_JS.count("try {") + BASE_TEMPLATE.count("try {")
        caught = THEME_JS.count("catch (error)") + BASE_TEMPLATE.count("catch (error)")

        self.assertEqual(THEME_JS.count("localStorage") , 2)
        self.assertEqual(BASE_TEMPLATE.count("localStorage"), 1)
        self.assertEqual(guarded, 3)
        self.assertEqual(caught, 3)

    def test_unknown_stored_value_falls_back_to_system(self):
        """Значение в хранилище правится руками и переживает смену версий."""
        self.assertIn('ORDER.indexOf(value) === -1 ? "system" : value', THEME_JS)
