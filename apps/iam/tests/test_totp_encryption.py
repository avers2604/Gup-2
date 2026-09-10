"""Шифрование секрета TOTP (ТЗ 4.7, по замечанию ревью) — apps/iam/totp_crypto.py,
User.totp_secret (свойство поверх totp_secret_encrypted/totp_secret_plaintext),
management-команда encrypt_totp_secrets."""
from io import StringIO

import pyotp
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from apps.iam import totp_crypto

from .. import services
from ..models import Department, User
from ..totp import generate_totp_secret


def _make_user(**kwargs):
    dept, _ = Department.objects.get_or_create(
        name="Служба движения", defaults={"level": Department.Level.SERVICE}
    )
    defaults = dict(
        personnel_number="0001", last_name="Иванов", first_name="Пётр",
        position="Водитель", department=dept, role=User.Role.READER,
        status=User.Status.ACTIVE,
    )
    defaults.update(kwargs)
    user = User(**defaults)
    user.set_password("Sup3r$ecret!Pass")
    user.save()
    return user


class TotpCryptoRoundTripTests(TestCase):
    def test_encrypt_then_decrypt_returns_original(self):
        secret = generate_totp_secret()
        ciphertext = totp_crypto.encrypt_totp_secret(secret)
        self.assertEqual(totp_crypto.decrypt_totp_secret(ciphertext), secret)

    def test_ciphertext_does_not_contain_plaintext(self):
        secret = generate_totp_secret()
        ciphertext = totp_crypto.encrypt_totp_secret(secret)
        self.assertNotIn(secret, ciphertext)

    def test_decrypting_garbage_raises_decryption_error(self):
        with self.assertRaises(totp_crypto.TotpSecretDecryptionError):
            totp_crypto.decrypt_totp_secret("not-a-valid-fernet-token")


class TotpSecretPropertyTests(TestCase):
    def test_freshly_set_secret_is_stored_only_encrypted(self):
        user = _make_user()
        secret = generate_totp_secret()
        user.totp_secret = secret
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.totp_secret, secret)
        self.assertNotEqual(user.totp_secret_encrypted, "")
        self.assertEqual(user.totp_secret_plaintext, "")

    def test_legacy_plaintext_secret_still_readable_via_property(self):
        # Строка, записанная напрямую в totp_secret_plaintext, как это было
        # бы у учётки, заведённой до появления шифрования, до прогона
        # encrypt_totp_secrets.
        user = _make_user()
        secret = generate_totp_secret()
        User.objects.filter(pk=user.pk).update(totp_secret_plaintext=secret)
        user.refresh_from_db()
        self.assertEqual(user.totp_secret, secret)

    def test_encrypted_value_takes_priority_over_legacy_plaintext(self):
        user = _make_user()
        old_secret = generate_totp_secret()
        new_secret = generate_totp_secret()
        User.objects.filter(pk=user.pk).update(totp_secret_plaintext=old_secret)
        user.refresh_from_db()
        user.totp_secret = new_secret
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.totp_secret, new_secret)

    def test_empty_secret_round_trips_as_empty(self):
        user = _make_user()
        self.assertEqual(user.totp_secret, "")


class EncryptTotpSecretsCommandTests(TestCase):
    def test_migrates_legacy_plaintext_secret_to_encrypted(self):
        user = _make_user()
        secret = generate_totp_secret()
        User.objects.filter(pk=user.pk).update(totp_secret_plaintext=secret)

        out = StringIO()
        call_command("encrypt_totp_secrets", stdout=out)

        user.refresh_from_db()
        self.assertEqual(user.totp_secret_plaintext, "")
        self.assertNotEqual(user.totp_secret_encrypted, "")
        self.assertEqual(user.totp_secret, secret)
        self.assertIn("1", out.getvalue())

    def test_running_twice_is_idempotent(self):
        user = _make_user()
        secret = generate_totp_secret()
        User.objects.filter(pk=user.pk).update(totp_secret_plaintext=secret)

        call_command("encrypt_totp_secrets", stdout=StringIO())
        out = StringIO()
        call_command("encrypt_totp_secrets", stdout=out)

        user.refresh_from_db()
        self.assertEqual(user.totp_secret, secret)
        self.assertIn("0", out.getvalue())

    def test_users_without_legacy_secret_are_untouched(self):
        _make_user()
        out = StringIO()
        call_command("encrypt_totp_secrets", stdout=out)
        self.assertIn("0", out.getvalue())


class TotpEncryptedLoginFlowTests(TestCase):
    """Полный флоу вход + подтверждение TOTP-кода должен продолжать
    работать без изменений — свойство totp_secret прозрачно для
    apps.iam.services/apps.iam.totp (проверено ЖИВЫМ end-to-end флоу, не
    только на уровне модели)."""

    def setUp(self):
        self.client = Client()
        self.user = _make_user()
        self.secret = generate_totp_secret()
        self.user.totp_secret = self.secret
        self.user.totp_enabled = True
        self.user.save()

    def test_enrollment_and_login_with_encrypted_secret(self):
        result = services.check_credentials(
            None, personnel_number=self.user.personnel_number, password="Sup3r$ecret!Pass",
        )
        self.assertTrue(result.totp_required)
        ticket = services.make_totp_pending_ticket(result.user)

        code = pyotp.TOTP(self.secret).now()
        verified_user = services.verify_totp_login(ticket=ticket, code=code)
        self.assertEqual(verified_user.pk, self.user.pk)

    def test_web_login_view_end_to_end_with_encrypted_secret(self):
        response = self.client.post(
            reverse("iam:login"), {"personnel_number": "0001", "password": "Sup3r$ecret!Pass"},
        )
        self.assertEqual(response.status_code, 302)

        code = pyotp.TOTP(self.secret).now()
        response = self.client.post(reverse("iam:login-verify-totp"), {"code": code})
        self.assertEqual(response.status_code, 302)
