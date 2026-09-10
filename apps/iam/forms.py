from django import forms

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
