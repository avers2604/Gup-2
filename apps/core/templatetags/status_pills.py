"""Класс статус-плашки по значению статуса.

Маппинг «статус → CSS-класс» жил цепочками `{% if %}` сразу в четырёх
шаблонах (реестр НРД, карточка, форма смены статуса, выдача поиска), и они
успели разойтись: три из четырёх знали про «Аннулирован» (и красили его
красным заодно с «Утратил силу»), а выдача поиска про него не знала вовсе —
аннулированный документ в результатах выглядел черновиком. Пока правило
размазано по шаблонам, новый статус требует правки каждого из них, и забыть
один — вопрос времени.
"""
from django import template

register = template.Library()

#: Значения — из NormativeDocument.Status и Template.Status. Словарь, а не
#: цепочка условий: отсутствующий ключ должен быть виден как отсутствующий,
#: а не молча попадать в «иначе — черновик».
_PILL_CLASSES = {
    "active": "status-pill--active",
    "active_amended": "status-pill--amended",
    "revoked": "status-pill--revoked",
    "annulled": "status-pill--annulled",
    "draft": "status-pill--draft",
    "archived": "status-pill--archived",
    "superseded": "status-pill--archived",
}

_FALLBACK = "status-pill--draft"


@register.filter
def status_pill(status) -> str:
    """CSS-класс плашки для статуса документа или бланка.

    Неизвестный статус получает нейтральную плашку, а не падает: статус
    приходит из БД, и рассыпать из-за него страницу реестра — хуже, чем
    показать документ нейтрально. Сам текст статуса рядом остаётся верным,
    потому что берётся из `get_status_display`.
    """
    return _PILL_CLASSES.get(str(status or ""), _FALLBACK)
