"""Формы Web GUI рабочих мест (ТЗ 4.1). Валидация ввода, без бизнес-логики
— тот же принцип, что и в apps/iam/forms.py."""
from django import forms

from apps.iam.models import Department

from .models import NormativeDocument

# .field — класс дизайн-системы (static/css/components.css), тот же
# паттерн ручного проставления, что в apps/iam/forms.py и
# apps/search_ocr/forms.py.
_FIELD_ATTRS = {"class": "field"}


class DocumentFilterForm(forms.Form):
    """Фильтры реестра НРД. Все поля необязательны: пустая форма — это
    валидная форма «показать всё, что доступно», а не ошибка ввода."""

    doc_type = forms.ChoiceField(
        label="Вид документа", required=False,
        choices=[("", "Любой")] + list(NormativeDocument.DocType.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    status = forms.ChoiceField(
        label="Статус", required=False,
        choices=[("", "Любой")] + list(NormativeDocument.Status.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    issuer_dept = forms.ModelChoiceField(
        label="Издавшее подразделение", required=False,
        queryset=Department.objects.all(), empty_label="Любое",
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    effective_from = forms.DateField(
        label="Действует с", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )
    effective_to = forms.DateField(
        label="Действует по", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("effective_from")
        date_to = cleaned.get("effective_to")
        if date_from and date_to and date_from > date_to:
            # Иначе фильтр молча вернул бы пустой список, и пользователь
            # решил бы, что документов нет, а не что интервал перевёрнут.
            raise forms.ValidationError("Начало периода позже его окончания.")
        return cleaned
