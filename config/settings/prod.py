import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

_required_production_settings = {
    "SECRET_KEY": os.environ.get("SECRET_KEY"),
    "POSTGRES_DB": os.environ.get("POSTGRES_DB"),
    "POSTGRES_USER": os.environ.get("POSTGRES_USER"),
    "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
    "MINIO_ACCESS_KEY": os.environ.get("MINIO_ACCESS_KEY"),
    "MINIO_SECRET_KEY": os.environ.get("MINIO_SECRET_KEY"),
    "MINIO_ENDPOINT_URL": os.environ.get("MINIO_ENDPOINT_URL"),
}
_missing_production_settings = [
    name for name, value in _required_production_settings.items() if not value
]
if _missing_production_settings:
    raise ImproperlyConfigured(
        "Production settings require: " + ", ".join(_missing_production_settings)
    )

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# MinIO — распределённое S3-хранилище по ТЗ 4.9. Два бакета с разными
# политиками (STACK.md → «Разделение политик хранения MinIO»): "originals"
# создаётся с Object Locking/WORM (см. deploy/minio/init-bucket.sh) и не
# допускает перезаписи; "working" — обычный бакет для редактируемых копий
# и бланков, которые правомерно заменяются при минорной корректировке
# (ТЗ 4.3.1). Никогда не указывать один и тот же MINIO_BUCKET_* для обоих.
_s3_common_options = {
    "access_key": os.environ.get("MINIO_ACCESS_KEY"),
    "secret_key": os.environ.get("MINIO_SECRET_KEY"),
    "endpoint_url": os.environ.get("MINIO_ENDPOINT_URL"),
    "default_acl": "private",
    "file_overwrite": False,
}

STORAGES["originals"] = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        **_s3_common_options,
        "bucket_name": os.environ.get("MINIO_BUCKET_ORIGINALS", "bz-get-originals"),
    },
}
STORAGES["working"] = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        **_s3_common_options,
        "bucket_name": os.environ.get("MINIO_BUCKET_WORKING", "bz-get-working"),
    },
}
# "default" — сознательно указывает на заменяемый бакет "working", а не на
# защищённый "originals": любое будущее FileField без явного storage= не
# должно случайно попасть под WORM-блокировку.
STORAGES["default"] = STORAGES["working"]
