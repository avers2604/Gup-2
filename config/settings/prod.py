import os

from .base import *  # noqa: F401,F403

DEBUG = False

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# MinIO — распределённое S3-хранилище по ТЗ 4.9 (WORM Object Locking
# настраивается на стороне бакета средствами MinIO, вне Django).
STORAGES["default"] = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "access_key": os.environ.get("MINIO_ACCESS_KEY"),
        "secret_key": os.environ.get("MINIO_SECRET_KEY"),
        "bucket_name": os.environ.get("MINIO_BUCKET_NAME", "bz-get-documents"),
        "endpoint_url": os.environ.get("MINIO_ENDPOINT_URL"),
        "default_acl": "private",
        "file_overwrite": False,
    },
}
