"""Фильтры журнала аудита (ТЗ 4.7). Только валидация ввода."""
from django import forms

from .models import AuditLog

_FIELD_ATTRS = {"class": "field"}


class AuditFilterForm(forms.Form):
    event_type = forms.ChoiceField(
        label="Тип события", required=False,
        choices=[("", "Любой")] + list(AuditLog.EventType.choices),
        widget=forms.Select(attrs=_FIELD_ATTRS),
    )
    actor_personnel_number = forms.CharField(
        label="Табельный номер", required=False, max_length=32,
        widget=forms.TextInput(attrs=_FIELD_ATTRS),
    )
    object_id = forms.CharField(
        label="Объект", required=False, max_length=64,
        widget=forms.TextInput(attrs={**_FIELD_ATTRS, "placeholder": "рег. номер или id"}),
    )
    date_from = forms.DateField(
        label="С даты", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )
    date_to = forms.DateField(
        label="По дату", required=False,
        widget=forms.DateInput(attrs={**_FIELD_ATTRS, "type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError("Начало периода позже его окончания.")
        return cleaned
