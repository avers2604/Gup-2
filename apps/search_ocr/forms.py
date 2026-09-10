from django import forms

from .models import ThesaurusCategory, ThesaurusService

# .field/.field-label — веб-порт DESIGN.md (static/css/components.css),
# тот же паттерн, что и apps/iam/forms.py.
_FIELD_ATTRS = {"class": "field"}


class SearchForm(forms.Form):
    """GET-форма (поиск должен быть закладываемой в закладки/шареабельной
    ссылкой — стандартная семантика поиска, не должна требовать POST)."""

    q = forms.CharField(
        label="Поисковый запрос", required=False,
        widget=forms.TextInput(attrs={**_FIELD_ATTRS, "placeholder": "Например: приказ по ТБ"}),
    )
    # Необязательные фасеты — используются ТОЛЬКО для разрешения
    # неоднозначных аббревиатур тезауруса при расширении запроса
    # (apps.search_ocr.search.expand_query), не для фильтрации самих
    # карточек НРД (у которых нет полей category/service тезаурусного
    # словаря) — см. STACK.md.
    category = forms.ChoiceField(
        label="Уточнить категорию термина", required=False,
        choices=[("", "Любая")] + list(ThesaurusCategory.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    service = forms.ChoiceField(
        label="Уточнить службу", required=False,
        choices=[("", "Любая")] + list(ThesaurusService.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
