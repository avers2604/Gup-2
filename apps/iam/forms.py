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


class TotpCodeForm(forms.Form):
    code = forms.CharField(
        label="Код из приложения-аутентификатора", max_length=10,
        widget=forms.TextInput(attrs={**_FIELD_ATTRS, "autocomplete": "one-time-code", "inputmode": "numeric"}),
    )
