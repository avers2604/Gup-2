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
_MODIFIER_IN_TEMPLATE = re.compile(r"\b([a-z]+(?:-[a-z]+)*(?:--|__)[a-z-]+)\b")
_CUSTOM_PROPERTY_USE = re.compile(r"var\(\s*(--[a-z0-9-]+)")
# Определение ищется в любом месте строки, а не только в её начале:
# однострочное правило вида `.row--tight { --row-gap: var(--space-2); }`
# — нормальный CSS, и тест не должен требовать переносов ради себя.
# Спутать определение с обращением нельзя: в `var(--x)` и `var(--x, y)`
# после имени идёт скобка или запятая, но не двоеточие.
_CUSTOM_PROPERTY_DEF = re.compile(r"(--[a-z0-9-]+)\s*:")
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


class LayoutComesFromClassesTests(SimpleTestCase):
    """Раскладка задаётся классами, а не атрибутом style.

    До появления static/css/layout.css в шаблонах было 132 атрибута
    `style`, из них около семидесяти — чистая вертикальная ритмика вида
    `margin-top: var(--space-6)`. Шаг между блоками выбирался на глаз
    отдельно в каждом файле, изменить ритм страницы означало править
    семьдесят мест, и никакая проверка за значением в атрибуте не следила.

    Тест сторожит именно раскладку, а не любой инлайн-стиль: демонстрация
    самого токена на витрине (`background: var(--color-navy)` в таблице
    палитры) и ширина заполнения прогресс-бара — это данные, а не вёрстка,
    и классами их выражать незачем.
    """

    #: Свойства, которым место в слое раскладки, а не в атрибуте элемента.
    _LAYOUT_PROPERTIES = (
        "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
        "margin-block", "margin-inline", "padding-top", "padding-bottom",
        "display", "flex-wrap", "align-items", "justify-content",
        "grid-template-columns", "max-width", "min-width",
    )

    def test_no_layout_declarations_in_style_attributes(self):
        offenders = {}
        for path in _template_files():
            for match in re.finditer(r'style="([^"]*)"', path.read_text(encoding="utf-8")):
                for declaration in match.group(1).split(";"):
                    prop = declaration.split(":", 1)[0].strip().lower()
                    if prop in self._LAYOUT_PROPERTIES:
                        offenders.setdefault(path.name, set()).add(prop)

        self.assertEqual(
            offenders,
            {},
            "Раскладка задана инлайном вместо классов static/css/layout.css: "
            + "; ".join(f"{name} ({', '.join(sorted(props))})" for name, props in sorted(offenders.items())),
        )
