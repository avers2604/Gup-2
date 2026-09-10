import re

from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

_SPECIAL_CHARS = re.compile(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]")


class SpecialCharacterValidator:
    """Требует хотя бы один спецсимвол — парольная политика ТЗ 4.7."""

    def validate(self, password, user=None):
        if not _SPECIAL_CHARS.search(password):
            raise ValidationError(
                "Пароль должен содержать хотя бы один спецсимвол.",
                code="password_no_special_char",
            )

    def get_help_text(self):
        return "Пароль должен содержать хотя бы один спецсимвол."


class PasswordHistoryValidator:
    """Запрет повторного использования последних N паролей (решение
    Заказчика: «история 10 паролей»). N — apps.iam.models.PASSWORD_HISTORY_DEPTH,
    не задублирован здесь отдельной константой.

    История наполняется из User.save() (см. его docstring про
    password_changed_at/PasswordHistoryEntry) — этому валидатору не нужно
    знать НИЧЕГО о том, как и когда она пополняется, только читать её.
    Без user (например Django management-команда без привязанного
    пользователя, или совсем новый ещё не сохранённый User без pk) —
    проверять не с чем, пропускаем: это не отказ политики, а её область
    применения — новому пользователю история физически неоткуда взяться."""

    def validate(self, password, user=None):
        if user is None or not getattr(user, "pk", None):
            return
        # Локальный импорт — models.py импортирует .validators на уровне
        # модуля (AUTH_PASSWORD_VALIDATORS грузится из apps.iam.validators
        # до того, как apps.iam.models гарантированно готов); прямой импорт
        # наверху файла завёл бы цикл iam.models -> iam.validators -> iam.models.
        from .models import PasswordHistoryEntry

        for entry in PasswordHistoryEntry.objects.filter(user=user).order_by("-created_at"):
            if check_password(password, entry.password_hash):
                raise ValidationError(
                    "Этот пароль уже использовался ранее — нельзя повторно "
                    "использовать один из последних 10 паролей.",
                    code="password_reused",
                )

    def get_help_text(self):
        return "Пароль не должен совпадать с одним из последних 10 использованных паролей."


# Формат по образцу файла пакетного импорта персонала (ТЗ 4.6.2):
# tab_number — только цифры и дефис, длина 4-16.
personnel_number_validator = RegexValidator(
    regex=r"^[0-9-]{4,16}$",
    message="Табельный номер: только цифры и дефис, длина от 4 до 16 символов.",
    code="invalid_personnel_number",
)

# last_name/first_name/middle_name — кириллица, пробел, дефис (двойные фамилии).
cyrillic_name_validator = RegexValidator(
    regex=r"^[А-ЯЁа-яё]+(?:[\- ][А-ЯЁа-яё]+)*$",
    message="Допустимы только кириллица, пробел и дефис.",
    code="invalid_cyrillic_name",
)
