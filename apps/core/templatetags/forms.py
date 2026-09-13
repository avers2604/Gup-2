"""Рендер виджета поля с правильной ARIA-разметкой.

Атрибуты проставляются здесь, а не в определениях виджетов и не в каждом
шаблоне формы: иначе о них нужно помнить в каждой форме проекта, и забыть
— вопрос времени. Без `aria-invalid` скринридер не сообщает, что поле
отвергнуто, а красный текст под ним остаётся чисто зрительным признаком;
без `aria-describedby` подсказка и текст ошибки не связаны с полем и
читаются как отдельные абзацы неизвестно о чём.
"""
from django import template

register = template.Library()


@register.simple_tag
def field_widget(field):
    """Виджет поля с `aria-invalid` и `aria-describedby` по состоянию.

    `as_widget(attrs=...)` не затирает атрибуты, заданные при объявлении
    виджета (в проекте это `class="field"`), а дополняет их — Django
    сливает оба набора при отрисовке.
    """
    attrs = {}
    described_by = []

    if field.help_text:
        described_by.append(f"{field.auto_id}-help")
    if field.errors:
        attrs["aria-invalid"] = "true"
        described_by.append(f"{field.auto_id}-error")
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)

    return field.as_widget(attrs=attrs)
