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


class AnchorButtonsAreNotUnderlinedTests(SimpleTestCase):
    """Кнопка, собранная из <a>, не должна выглядеть подчёркнутой ссылкой.

    Повод — первый живой просмотр интерфейса. Из 64 «кнопок» в шаблонах 41
    — это `<a class="btn …>`: «Зарегистрировать документ», «Сбросить»,
    «Добавить связь», вся пагинация. Браузер подчёркивает `<a>` по
    умолчанию, `.btn` подчёркивание не гасил — и заливка выходила с
    подчёркнутой подписью. Ни один тест этого не видел: класс существует,
    шаблон рендерится, CSS валиден.

    Обратная половина правила не менее важна. Настоящие ссылки — рег.
    номер в таблице реестра, пункт хлебных крошек — подчёркиваются
    намеренно: DESIGN.md, правило 1, цвет не может быть единственным
    носителем смысла. Поэтому тест требует не «нигде нет подчёркивания», а
    ровно двух вещей: `.btn` его гасит, базовое правило `a` — нет.
    """

    _RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

    def _declarations_of(self, selector: str) -> str:
        """Тело правила с ровно таким селектором (без комментариев)."""
        bodies = [
            body
            for head, body in self._RULE.findall(_css_text())
            if head.strip() == selector
        ]
        self.assertEqual(
            len(bodies), 1, f"Правило `{selector}` должно быть ровно одно, найдено {len(bodies)}"
        )
        return bodies[0]

    def test_btn_removes_the_link_underline(self):
        self.assertRegex(
            self._declarations_of(".btn"),
            r"text-decoration\s*:\s*none",
            "`.btn` не гасит подчёркивание — ссылки-кнопки рисуются с подчёркнутой подписью",
        )

    def test_plain_links_keep_their_underline(self):
        self.assertNotRegex(
            self._declarations_of("a"),
            r"text-decoration\s*:\s*none",
            "Базовое правило `a` сняло подчёркивание со всех ссылок разом: "
            "цвет остался их единственным признаком (DESIGN.md, правило 1)",
        )

    def test_every_anchor_styled_as_a_button_carries_the_btn_class(self):
        """Модификатор .btn--* без базового .btn остался бы подчёркнутым."""
        offenders = {}
        for path in _template_files():
            for tag in re.findall(r"<a\b[^>]*>", path.read_text(encoding="utf-8")):
                classes = re.search(r'class="([^"]*)"', tag)
                if classes and "btn--" in classes.group(1):
                    names = set(classes.group(1).split())
                    if "btn" not in names:
                        offenders.setdefault(path.name, []).append(classes.group(1))

        self.assertEqual(offenders, {}, f"Модификатор кнопки без базового класса: {offenders}")


class ExplanatoryTextHasReadableMeasureTests(SimpleTestCase):
    """Длина строки пояснительного текста ограничена.

    Ещё одна находка живого просмотра. Пояснение под заголовком «Сводные
    редакции» — 217 символов — растягивалось на всю ширину контейнера:
    на 1280 px это около 160 знаков в строке при норме 60–75. Глаз
    теряет начало следующей строки, и текст, написанный ради ясности,
    читается хуже, чем не написанный вовсе.

    Класс `.measure` (60ch) для этого и заведён в layout.css ещё в
    партии UI-1 — но применён тогда не был: посмотреть было не на что.
    Тест закрывает разрыв между «примитив есть» и «примитив применён».
    """

    #: Порог в знаках. Короткая подпись («Связей нет», «11.09.2026») в
    #: ограничении не нуждается — она и так не дотягивает до края.
    _LONG = 110

    _CAPTION = re.compile(r'<p class="caption([^"]*)">(.*?)</p>', re.S)
    _TEMPLATE_TAG = re.compile(r"\{[%{].*?[%}]\}", re.S)

    def _visible_length(self, raw: str) -> int:
        """Длина без тегов шаблона: `{{ x }}` — это не видимый текст."""
        return len(" ".join(self._TEMPLATE_TAG.sub("", raw).split()))

    def test_long_captions_are_limited_to_a_readable_measure(self):
        offenders = []
        for path in _template_files():
            for match in self._CAPTION.finditer(path.read_text(encoding="utf-8")):
                extra, body = match.group(1), match.group(2)
                length = self._visible_length(body)
                if length > self._LONG and "measure" not in extra.split():
                    offenders.append(f"{path.name}: {length} знаков — {body.strip()[:60]}…")

        self.assertEqual(
            offenders,
            [],
            "Пояснение длиннее 110 знаков без .measure — строка растянется "
            "на всю ширину контейнера:\n" + "\n".join(offenders),
        )

    def test_measure_is_defined_and_is_a_width_limit(self):
        """Класс должен существовать и ограничивать именно ширину."""
        self.assertRegex(
            _css_text(),
            r"\.measure\s*\{[^}]*max-width\s*:\s*\d+ch",
            ".measure должен ограничивать ширину в ch — единице, привязанной к кеглю",
        )
