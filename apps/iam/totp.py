"""
TOTP (RFC 6238) — второй фактор входа для ролей, где он обязателен
(`User.requires_totp`, ТЗ 4.7). Чистые функции над `pyotp`, без обращения
к БД — вызывающий код (services/views) сам решает, когда читать/писать
`User.totp_secret`/`totp_enabled`.
"""
import pyotp

ISSUER_NAME = "АИС «БЗ ГЭТ»"

# Допуск в ±1 шаг (30 секунд каждый) на рассинхронизацию часов клиента —
# стандартная практика для TOTP-аутентификаторов (Google Authenticator и
# аналоги допускают дрейф до минуты без предупреждения пользователю).
_VALID_WINDOW = 1


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(*, secret: str, personnel_number: str) -> str:
    """otpauth://-URI для QR-кода в приложении-аутентификаторе. Рендеринг
    самого QR — задача клиента (веб/мобильного), не этого модуля."""
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=personnel_number, issuer_name=ISSUER_NAME,
    )


def verify_totp_code(*, secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.totp.TOTP(secret).verify(code, valid_window=_VALID_WINDOW)
