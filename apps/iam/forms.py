from django import forms
from django.contrib.auth.forms import PasswordChangeForm as DjangoPasswordChangeForm

from .models import User

# .field — класс из static/css/components.css (веб-порт DESIGN.md),
# применяется вручную к виджетам: голый {{ form.x }} без него рендерит
# Django-дефолтный <input> без стилей дизайн-системы.
_FIELD_ATTRS = {"class": "field"}


class LoginForm(forms.Form):
    personnel_number = forms.CharField(
        label="Табельный номер", widget=forms.TextInput(attrs=_FIELD_ATTRS),
    )
    password = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs=_FIELD_ATTRS),
    )
    # Дифференцированный таймаут неактивности (решение Заказчика): 15 минут
    # вместо 30, если это компьютер общего доступа — см.
    # apps/iam/views.py (request.session.set_expiry()).
    shared_terminal = forms.BooleanField(
        label="Это компьютер общего доступа (терминал)", required=False,
        widget=forms.CheckboxInput(),
    )


class TotpCodeForm(forms.Form):
    code = forms.CharField(
        label="Код из приложения-аутентификатора", max_length=10,
        widget=forms.TextInput(attrs={**_FIELD_ATTRS, "autocomplete": "one-time-code", "inputmode": "numeric"}),
    )


class PasswordChangeForm(DjangoPasswordChangeForm):
    """Django-стандартная форма (old_password + password1/password2,
    validate_password() с нашими AUTH_PASSWORD_VALIDATORS — включая
    SpecialCharacterValidator/PasswordHistoryValidator) — по запросу
    ревью анти-фрода: единственная вьюха смены пароля в проекте (до этой
    партии её не было вовсе ни в одном контуре, см. STACK.md). save()
    снимает status=PASSWORD_CHANGE_REQUIRED, если он был выставлен —
    смена пароля и есть то самое требуемое действие; на ACTIVE/BLOCKED
    он не влияет."""

    error_messages = {
        **DjangoPasswordChangeForm.error_messages,
        "password_incorrect": "Текущий пароль указан неверно.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update(_FIELD_ATTRS)

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.status == User.Status.PASSWORD_CHANGE_REQUIRED:
            user.status = User.Status.ACTIVE
        if commit:
            user.save()
        return user
