"""Подсветка текущего раздела в шапке.

Раздел определяется по имени сопоставленного маршрута, а не по префиксу
URL. Префикс здесь не работает: «Документы», «Сводные редакции» и
«Вычитка OCR» — три разных пункта меню, живущих под одним `/documents/`,
и подсветка по префиксу зажгла бы все три сразу.

Принадлежность объявляется явным перечислением, а не выводится из имени:
карточка документа (`documents:detail`) относится к «Документам», а
`documents:consolidated_detail` — к «Сводным редакциям», и никакое общее
правило этого не угадает.
"""
from django import template
from django.utils.html import format_html

register = template.Library()


def _current_route(context) -> str:
    """Полное имя текущего маршрута, например `documents:detail`."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    return match.view_name or ""


@register.simple_tag(takes_context=True)
def nav_active(context, *routes) -> str:
    """`aria-current="page"` и класс, если текущий маршрут — в разделе.

    Возвращается именно `aria-current`, а не только класс: пункт меню,
    выделенный одним цветом, для скринридера ничем не отличается от
    остальных, а правило DESIGN.md прямо запрещает делать цвет
    единственным носителем смысла.

    Шаблон `namespace:*` покрывает весь раздел целиком — для приложений,
    где все маршруты принадлежат одному пункту меню.
    """
    current = _current_route(context)
    if not current:
        return ""

    namespace = current.split(":")[0]
    for route in routes:
        if route == current or (route.endswith(":*") and route[:-1] == f"{namespace}:"):
            # Тег возвращает атрибуты, а simple_tag по умолчанию экранирует
            # результат, превращая кавычки в &quot; — разметка оставалась
            # валидной, но атрибуты не применялись. format_html, а не
            # mark_safe: подставлять сюда нечего, зато format_html не
            # открывает дверь для этого в будущем и не требует исключения
            # в bandit.
            return format_html(' class="is-active" aria-current="page"')
    return ""
