import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[3]


class ProductionStorageSecurityTests(TestCase):
    def test_s3_presigned_urls_have_explicit_short_ttl(self):
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.prod",
                "SECRET_KEY": "s" * 60,
                "TOTP_ENCRYPTION_KEY": "MTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTE=",
                "CACHE_URL": "redis://localhost:6379/2",
                "POSTGRES_DB": "bz_get",
                "POSTGRES_USER": "bz_get_app",
                "POSTGRES_PASSWORD": "runtime-secret",
                "POSTGRES_HOST": "localhost",
                "MINIO_ACCESS_KEY": "bz_get_app",
                "MINIO_SECRET_KEY": "minio-app-secret",
                "MINIO_ENDPOINT_URL": "http://localhost:9000",
                "MINIO_BUCKET_ORIGINALS": "originals",
                "MINIO_BUCKET_WORKING": "working",
                "MINIO_PRESIGNED_URL_TTL_SECONDS": "300",
                "ALLOWED_HOSTS": "localhost",
            }
        )
        code = (
            "import json; import config.settings.prod as p; "
            "print(json.dumps({k: p.STORAGES[k]['OPTIONS'].get('querystring_expire') "
            "for k in ('originals','working')}))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        values = json.loads(completed.stdout.strip())
        self.assertEqual(values, {"originals": 300, "working": 300})
