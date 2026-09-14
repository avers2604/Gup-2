import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSettingsValidationTests(SimpleTestCase):
    def _environment(self, *, secret_key):
        environment = os.environ.copy()
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.prod",
                "SECRET_KEY": secret_key,
                "TOTP_ENCRYPTION_KEY": "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
                "CACHE_URL": "redis://localhost:6379/2",
                "POSTGRES_DB": "bz_get",
                "POSTGRES_USER": "bz_get",
                "POSTGRES_PASSWORD": "test-postgres-password",
                "POSTGRES_HOST": "localhost",
                "POSTGRES_PORT": "5432",
                "MINIO_ACCESS_KEY": "test-access",
                "MINIO_SECRET_KEY": "test-secret",
                "MINIO_ENDPOINT_URL": "http://localhost:9000",
                "MINIO_BUCKET_ORIGINALS": "test-originals",
                "MINIO_BUCKET_WORKING": "test-working",
                "ALLOWED_HOSTS": "example.test",
                "DEBUG": "0",
            }
        )
        return environment

    def _import_production_settings(self, *, secret_key):
        return subprocess.run(
            [sys.executable, "-c", "import config.settings.prod"],
            env=self._environment(secret_key=secret_key),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_public_example_secret_key_is_rejected(self):
        result = self._import_production_settings(
            secret_key="replace-with-a-random-production-secret-key-at-least-50-characters-long"
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SECRET_KEY", result.stdout + result.stderr)

    def test_non_example_long_secret_still_loads(self):
        result = self._import_production_settings(secret_key="x" * 64)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
