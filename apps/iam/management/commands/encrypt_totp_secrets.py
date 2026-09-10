from django.core.management.base import BaseCommand

from apps.iam.models import User


class Command(BaseCommand):
    """Одноразовая миграция данных (по запросу ревью): переносит секреты
    TOTP, сохранённые до появления шифрования (User.totp_secret_plaintext),
    в зашифрованное поле (User.totp_secret_encrypted, ключ —
    settings.TOTP_ENCRYPTION_KEY). Идемпотентна: повторный запуск не
    находит уже перенесённых строк (totp_secret_plaintext у них уже
    пуст) и ничего не делает. Открытый текст стирается сразу после
    переноса — само поле удаляется отдельной миграцией позже, не в этой
    партии (см. STACK.md)."""

    help = "Шифрует ранее сохранённые в открытом виде секреты TOTP (одноразово, идемпотентно)."

    def handle(self, *args, **options):
        candidates = User.objects.exclude(totp_secret_plaintext="")

        count = 0
        for user in candidates:
            plaintext = user.totp_secret_plaintext
            user.totp_secret = plaintext
            user.save(update_fields=["totp_secret_encrypted", "totp_secret_plaintext"])
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Зашифровано секретов TOTP: {count}"))
