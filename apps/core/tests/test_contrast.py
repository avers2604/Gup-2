"""Контраст веб-палитры по WCAG AA.

DESIGN.md обещает: «Обе темы проходят проверку контраста автоматически»
и «Любое изменение палитры прогоняется тестом контраста». Для версии на
Tkinter это правда — там такой тест есть. В веб-слое его не было ни
одного, и обещание оставалось неверным ровно настолько, насколько веб
считается частью системы.

Цена отсутствия проверки уже проявилась: плашка «Аннулирован» и гриф ДСП
заведены парами цветов, которые никто не считал.

Пары НЕ перечисляются руками, а извлекаются из самого CSS: правило,
задающее и `color`, и `background`, — это и есть пара «текст на фоне».
Перечень руками отстал бы от кода на первом же новом компоненте, то есть
повторил бы исходную ошибку. Пары, где фон приходит от родителя (текст на
поверхности карточки, приглушённый текст на фоне страницы), CSS вывести
не даёт — они перечислены отдельно и явно.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"

#: Порог WCAG AA для обычного текста. Крупный текст допускает 3.0, но в
#: интерфейсе цветные заливки несут именно мелкие подписи — плашки,
#: кнопки, шапку, — поэтому послабление здесь не применяется.
WCAG_AA_NORMAL_TEXT = 4.5

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
_VAR_USE = re.compile(r"var\(\s*(--[a-z0-9-]+)")
_DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+)")


def _relative_luminance(hex_colour: str) -> float:
    """Относительная яркость по WCAG 2.1, формула 1.4.3."""
    value = _HEX.match(hex_colour).group(1)
    channels = []
    for offset in (0, 2, 4):
        raw = int(value[offset:offset + 2], 16) / 255
        channels.append(raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """Отношение контраста двух непрозрачных цветов: от 1 до 21."""
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _unwrap_media(source: str) -> str:
    """Развернуть `@media` так, чтобы вложенное правило стало верхнеуровневым.

    Плоский разбор правил не видит блок внутри медиазапроса: селектор
    `@media (prefers-color-scheme: dark)` содержит вложенные фигурные
    скобки, а `:root:not([data-theme="light"])` внутри него не содержит
    слова «dark». Из-за этого первая редакция теста нашла одно объявление
    тёмной темы вместо двух — и молча проверила бы только половину.
    Условие медиазапроса приписывается к селектору, чтобы тема осталась
    различимой.
    """
    result = []
    index = 0
    while True:
        at = source.find("@media", index)
        if at == -1:
            result.append(source[index:])
            return "".join(result)
        result.append(source[index:at])
        condition = source[at:source.index("{", at)]
        depth, cursor = 0, source.index("{", at)
        while True:
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        body = source[source.index("{", at) + 1:cursor]
        marker = "dark" if "dark" in condition else "light"
        result.append(body.replace(":root", f":root /*{marker}*/", 1))
        index = cursor + 1


def _tokens() -> dict[str, dict[str, str]]:
    """Значения токенов по темам: светлая и тёмная.

    Тёмная тема объявлена дважды — под `prefers-color-scheme` и под
    `[data-theme="dark"]`; отдельный тест проверяет, что объявления не
    разошлись.
    """
    source = _unwrap_media(
        _COMMENT.sub("", (CSS_DIR / "tokens.css").read_text(encoding="utf-8"))
    )
    themes: dict[str, dict[str, str]] = {"light": {}, "dark": {}}
    for selector, body in _RULE.findall(source):
        target = "dark" if "dark" in selector else "light"
        for name, value in _DECLARATION.findall(body):
            themes[target].setdefault(name, value.strip())
    # Тёмная тема переопределяет только поверхности; фирменные цвета общие.
    themes["dark"] = {**themes["light"], **themes["dark"]}
    return themes


def _resolve(value: str, tokens: dict[str, str]) -> str | None:
    """Цвет в HEX или None, если значение не цвет (transparent, градиент)."""
    value = value.strip()
    reference = _VAR_USE.search(value)
    if reference:
        resolved = tokens.get(reference.group(1))
        return _resolve(resolved, tokens) if resolved else None
    return value if _HEX.match(value) else None


def _rule_pairs() -> list[tuple[str, str, str]]:
    """(селектор, значение цвета, значение фона) из правил, задающих оба."""
    pairs = []
    for name in ("components.css", "layout.css", "base.css"):
        source = _COMMENT.sub("", (CSS_DIR / name).read_text(encoding="utf-8"))
        for selector, body in _RULE.findall(source):
            background = re.search(r"background(?:-color)?:\s*([^;]+)", body)
            foreground = re.search(r"(?<!-)\bcolor:\s*([^;]+)", body)
            if background and foreground:
                pairs.append(
                    (" ".join(selector.split()), foreground.group(1), background.group(1))
                )
    return pairs


#: Пары, где фон задаётся не тем же правилом, а родителем. CSS их не
#: выводит, поэтому они названы явно — это те сочетания, которые реально
#: встречаются на экранах.
INHERITED_PAIRS = (
    ("основной текст на фоне страницы", "--text", "--bg"),
    ("основной текст на поверхности карточки", "--text", "--surface"),
    ("приглушённый текст на фоне страницы", "--text-muted", "--bg"),
    ("приглушённый текст на поверхности карточки", "--text-muted", "--surface"),
    ("приглушённый текст на второй поверхности", "--text-muted", "--surface-2"),
    ("ошибка поля на поверхности карточки", "--text-danger", "--surface"),
    ("ссылка на поверхности карточки", "--text-link", "--surface"),
    ("ссылка на фоне страницы", "--text-link", "--bg"),
)


class PaletteContrastTests(SimpleTestCase):
    def test_every_colour_pair_declared_in_css_passes_wcag_aa(self):
        """Правило, задающее и цвет, и фон, — это пара «текст на фоне»."""
        failures = []
        for theme, tokens in _tokens().items():
            for selector, foreground, background in _rule_pairs():
                text = _resolve(foreground, tokens)
                surface = _resolve(background, tokens)
                if text is None or surface is None:
                    continue  # transparent, градиент, наследуемый фон
                ratio = contrast_ratio(text, surface)
                if ratio < WCAG_AA_NORMAL_TEXT:
                    failures.append(f"{theme}: {selector} — {ratio:.2f} ({text} на {surface})")

        self.assertEqual(failures, [], "Контраст ниже WCAG AA:\n" + "\n".join(failures))

    def test_inherited_pairs_pass_wcag_aa(self):
        """Текст на поверхности: фон приходит от родителя, CSS его не связывает."""
        failures = []
        for theme, tokens in _tokens().items():
            for label, foreground, background in INHERITED_PAIRS:
                ratio = contrast_ratio(tokens[foreground], tokens[background])
                if ratio < WCAG_AA_NORMAL_TEXT:
                    failures.append(f"{theme}: {label} — {ratio:.2f}")

        self.assertEqual(failures, [], "Контраст ниже WCAG AA:\n" + "\n".join(failures))

    def test_pure_accent_is_never_used_under_text(self):
        """Ключевое отступление DESIGN.md: #009CBC даёт 3.24 с белым.

        Поэтому чистая бирюза применяется только там, где текста нет
        (индикаторы, полосы, подчёркивание активного пункта), а под
        подписи идёт затемнённый accent_fill.
        """
        for selector, foreground, background in _rule_pairs():
            if "--color-accent)" in background:
                self.fail(f"{selector}: подпись на чистой бирюзе даёт контраст 3.24")
        self.assertLess(
            contrast_ratio("#FFFFFF", "#009CBC"), WCAG_AA_NORMAL_TEXT,
            "если это перестало быть правдой, отступление в DESIGN.md пора пересмотреть",
        )

    def test_both_dark_theme_declarations_agree(self):
        """Тёмная тема объявлена дважды — под медиазапрос и под атрибут.

        Разойдясь, они дали бы разный вид одной темы в зависимости от
        того, выбрана она вручную или взята из системной настройки.
        """
        source = _unwrap_media(
            _COMMENT.sub("", (CSS_DIR / "tokens.css").read_text(encoding="utf-8"))
        )
        blocks = [
            dict(_DECLARATION.findall(body))
            for selector, body in _RULE.findall(source)
            if "dark" in selector
        ]
        self.assertEqual(len(blocks), 2, "ожидались ровно два объявления тёмной темы")
        self.assertEqual(
            {k: v.strip() for k, v in blocks[0].items()},
            {k: v.strip() for k, v in blocks[1].items()},
        )

    def test_contrast_ratio_matches_the_values_quoted_in_design_md(self):
        """Проверка самой формулы по числам, записанным в DESIGN.md."""
        self.assertAlmostEqual(contrast_ratio("#FFFFFF", "#009CBC"), 3.24, places=2)
        self.assertAlmostEqual(contrast_ratio("#FFFFFF", "#00809A"), 4.61, places=2)
