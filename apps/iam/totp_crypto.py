"""Шифрование секрета TOTP при хранении в БД (ТЗ 4.7 — усиление аудита
контура «Вход и 2FA»). Ключ — settings.TOTP_ENCRYPTION_KEY, отдельный от
DJANGO_SECRET_KEY (см. config/settings/base.py). Используется моделью
User (apps/iam/models.py, свойство totp_secret)."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TotpSecretDecryptionError(Exception):
    """Зашифрованное значение повреждено или ключ TOTP_ENCRYPTION_KEY не тот,
    которым оно было зашифровано."""


def _fernet() -> Fernet:
    return Fernet(settings.TOTP_ENCRYPTION_KEY)


def encrypt_totp_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_totp_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TotpSecretDecryptionError(
            "Не удалось расшифровать секрет TOTP: ключ TOTP_ENCRYPTION_KEY "
            "не совпадает или данные повреждены."
        ) from exc
