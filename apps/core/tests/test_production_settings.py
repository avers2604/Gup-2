import os
import secrets
import subprocess
import sys
from cryptography.fernet import Fernet
from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def environment(self):
        env = os.environ.copy()
        env.update(DJANGO_SETTINGS_MODULE="config.settings.prod", SECRET_KEY=secrets.token_urlsafe(64),
                   TOTP_ENCRYPTION_KEY=Fernet.generate_key().decode(), POSTGRES_DB="ci",
                   POSTGRES_USER="ci", POSTGRES_PASSWORD="test-only", MINIO_ACCESS_KEY="ci",
                   MINIO_SECRET_KEY="test-only", MINIO_ENDPOINT_URL="http://localhost:9000",
                   CACHE_URL="redis://localhost:6379/2", ALLOWED_HOSTS="localhost",
                   PYTHON_DOTENV_DISABLED="1")
        return env

    def test_production_deployment_check(self):
        result = subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
                                env=self.environment(), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_or_development_totp_key_is_rejected(self):
        for key in ("", "5DVKKoTK7rYGmxTDJA3ASa9mGzjWwgqNl2HXSaq6sOA=", "invalid"):
            with self.subTest(key=key):
                env = self.environment()
                env["TOTP_ENCRYPTION_KEY"] = key
                result = subprocess.run([sys.executable, "manage.py", "check"], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("TOTP", result.stderr)
