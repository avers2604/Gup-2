"""Шаблоны не должны ссылаться на несуществующие классы и токены.

Повод конкретный: класс `.btn--secondary` не существовал никогда, но
использовался в шаблонах 18 раз — вся пагинация реестра, поиска, журнала
аудита, банка бланков, сводных редакций и очереди вычитки рисовалась без
фона и без рамки. Рядом в поиске стоял `var(--color-danger)` — токена с
таким именем в системе тоже нет, и единственный признак неверного запроса
выводился цветом по наследству.

Такую ошибку не ловит ни ruff, ни тесты вьюх: шаблон рендерится успешно,
CSS молча игнорирует неизвестный класс и неизвестную переменную. Поймать
её можно только сверкой имён — этим тест и занимается.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"

#: Модификаторы вида .btn--accent, .status-pill--active в шаблонах и в CSS.
_MODIFIER_IN_TEMPLATE = re.compile(r"\b([a-z]+(?:-[a-z]+)*--[a-z-]+)\b")
_CUSTOM_PROPERTY_USE = re.compile(r"var\(\s*(--[a-z0-9-]+)")
_CUSTOM_PROPERTY_DEF = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.M)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _css_text() -> str:
    """CSS без комментариев.

    Комментарии вырезаются не для красоты: первая редакция теста собирала
    имена «объявленных» классов из всего текста, включая комментарий, в
    котором `.btn--secondary` упомянут как раз для объяснения, что такого
    класса нет. Упоминание считалось объявлением, и тест проходил ровно на
    той регрессии, ради которой написан.
    """
    raw = "\n".join(path.read_text(encoding="utf-8") for path in sorted(CSS_DIR.glob("*.css")))
    return _CSS_COMMENT.sub(" ", raw)


def _template_files():
    return sorted(TEMPLATES_DIR.rglob("*.html"))


class TemplateClassesExistTests(SimpleTestCase):
    def test_every_modifier_class_is_defined_in_css(self):
        """Модификатор без определения — элемент без вида, а не ошибка."""
        css = _css_text()
        defined = set(_MODIFIER_IN_TEMPLATE.findall(css))

        missing = {}
        for path in _template_files():
            for name in _MODIFIER_IN_TEMPLATE.findall(path.read_text(encoding="utf-8")):
                if name not in defined:
                    missing.setdefault(name, set()).add(path.name)

        self.assertEqual(
            missing,
            {},
            "Шаблоны ссылаются на CSS-классы, которых нет в static/css: "
            + "; ".join(f"{name} ({', '.join(sorted(files))})" for name, files in sorted(missing.items())),
        )

    def test_every_custom_property_used_in_templates_is_defined(self):
        """var(--чего-то-нет) молча возвращает пустоту, и свойство не применяется."""
        css = _css_text()
        defined = set(_CUSTOM_PROPERTY_DEF.findall(css))

        missing = {}
        for path in _template_files():
            for name in _CUSTOM_PROPERTY_USE.findall(path.read_text(encoding="utf-8")):
                if name not in defined:
                    missing.setdefault(name, set()).add(path.name)

        self.assertEqual(
            missing,
            {},
            "Шаблоны используют токены, не объявленные в static/css: "
            + "; ".join(f"{name} ({', '.join(sorted(files))})" for name, files in sorted(missing.items())),
        )

    def test_every_custom_property_used_in_css_is_defined(self):
        css = _css_text()
        defined = set(_CUSTOM_PROPERTY_DEF.findall(css))
        used = set(_CUSTOM_PROPERTY_USE.findall(css))

        self.assertEqual(used - defined, set())
