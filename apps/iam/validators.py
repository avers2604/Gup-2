import re

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
