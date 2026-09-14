"""Поведение на узкой ширине.

До этого единственным медиазапросом по ширине был перенос списка
определений в одну колонку. Всё остальное рассчитано на ноутбук — система
и правда рабочая, за ней сидят с рабочих мест. Но карточку документа
открывают и по прямой ссылке из письма, в том числе с телефона.

Тест сторожит правила, а не пиксели: что порогов ровно столько, сколько
объявлено ниже с причиной, что решения приняты осознанно и что их не
отменили молча.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

CSS_DIR = Path(settings.BASE_DIR) / "static" / "css"
_ALL_CSS = "\n".join(
    path.read_text(encoding="utf-8") for path in sorted(CSS_DIR.glob("*.css"))
)
_WIDTH_QUERY = re.compile(r"@media\s*\(\s*(?:max|min)-width:\s*([^)]+)\)")


#: Пороги ширины и причина каждого. Пока порог был один, тест требовал
#: ровно его — и это было правильно: лишняя точка перелома означает ещё
#: одно место, где раскладка расходится. Редизайн под макет с боковым
#: меню добавил два, и требовать одного стало нельзя. Дисциплина,
#: однако, сохранена: порог, которого нет в этом перечне, валит тест, а
#: добавить его нельзя, не написав здесь, ради чего.
#:
#: Порогов ровно три, и они не взаимозаменяемы — каждый снимает свою
#: конкретную поломку, а не «делает адаптивно».
DECLARED_BREAKPOINTS = {
    # Боковое меню занимает 264 px. На планшете это четверть экрана, и
    # таблица реестра начинает переносить каждую ячейку задолго до того,
    # как включится телефонная раскладка. Ниже порога меню уезжает за
    # край и выдвигается кнопкой.
    "900px",
    # Панель фильтров (300 px) рядом с таблицей. Порог замерен, а не
    # выбран: при меню 264 px и панели 300 px на 1280 таблице остаётся
    # 646 px, а её семи колонкам нужно 738. Ниже 1400 панель встаёт над
    # таблицей, и ширина достаётся таблице целиком.
    "1400px",
    # Исходный порог: ширина, ниже которой две колонки списка
    # определений перестают помещаться. Не «телефон» — именно две
    # колонки.
    "560px",
}


class BreakpointTests(SimpleTestCase):
    def test_every_width_breakpoint_is_declared_with_a_reason(self):
        """Порог без причины — это порог, который поставили наугад."""
        found = set(_WIDTH_QUERY.findall(_ALL_CSS))

        self.assertEqual(
            found - DECLARED_BREAKPOINTS,
            set(),
            "В CSS появился порог ширины, не объявленный в DECLARED_BREAKPOINTS. "
            "Добавьте его туда вместе с причиной — или уложитесь в имеющиеся.",
        )
        self.assertEqual(
            DECLARED_BREAKPOINTS - found,
            set(),
            "Объявленный порог больше не используется в CSS — уберите его из перечня, "
            "иначе перечень начнёт расходиться с кодом.",
        )

    def test_viewport_meta_is_present(self):
        """Без него мобильный браузер рисует страницу в 980px и мельчит."""
        base = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn('name="viewport"', base)
        self.assertIn("width=device-width", base)

    def test_container_padding_shrinks_on_narrow_screens(self):
        """48px полей из 400 — это 12% ширины экрана под пустоту."""
        self.assertRegex(_ALL_CSS, r"\.container \{ padding-inline: var\(--space-3\)")

    def test_filter_field_takes_the_whole_row_when_narrow(self):
        """В ряду по два поля сжимаются до нечитаемой ширины."""
        self.assertIn(".row > .field-row {\n    flex-basis: 100%;", _ALL_CSS)

    def test_narrow_form_card_drops_its_width_limit(self):
        """Ограничение в 420px на экране в 400px прижимает форму к краю."""
        self.assertIn(".card--narrow, .card--medium { max-width: none; }", _ALL_CSS)

    def test_table_stays_a_table_and_the_reason_is_recorded(self):
        """Реестр читают построчно и сравнивают колонки между строками.

        «Карточка на строку» ломает именно это, а горизонтальный скролл
        сохраняет — но тогда скролл обязан быть заметен.
        """
        self.assertIn("Таблица НЕ превращается в карточки", _ALL_CSS)
        self.assertIn("min-width: 560px", _ALL_CSS)
        self.assertIn("--shadow-scroll-edge", _ALL_CSS)

    def test_nothing_forces_a_width_wider_than_a_phone(self):
        """Любой min-width больше 400px распирает страницу горизонтально.

        Исключение одно и намеренное — сама таблица: она живёт внутри
        .table-scroll, который её и прокручивает.
        """
        offenders = []
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", _ALL_CSS):
            selector = " ".join(match.group(1).split())
            width = re.search(r"min-width:\s*(\d+)px", match.group(2))
            if width and int(width.group(1)) > 400 and ".table" not in selector:
                offenders.append(f"{selector}: {width.group(1)}px")

        self.assertEqual(offenders, [], "распирают страницу: " + "; ".join(offenders))
