from django import forms

from .models import ThesaurusCategory, ThesaurusService

# .field/.field-label — веб-порт DESIGN.md (static/css/components.css),
# тот же паттерн, что и apps/iam/forms.py.
_FIELD_ATTRS = {"class": "field"}
MAX_QUERY_LENGTH = 200
MAX_QUERY_TERMS = 12


def _validate_query_limits(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise forms.ValidationError(
            f"Запрос слишком длинный: максимум {MAX_QUERY_LENGTH} символов."
        )
    term_count = len([term for term in cleaned.split() if term])
    if term_count > MAX_QUERY_TERMS:
        raise forms.ValidationError(
            f"Слишком много слов в запросе: максимум {MAX_QUERY_TERMS}."
        )
    return cleaned


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

    def clean_q(self):
        value = self.cleaned_data.get("q", "")
        if not value:
            return ""
        return _validate_query_limits(value)
