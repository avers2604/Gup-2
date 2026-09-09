import re

from django.core.exceptions import ValidationError

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
