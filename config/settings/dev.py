from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

STORAGES["default"]["OPTIONS"] = {"location": str(MEDIA_ROOT)}
STORAGES["originals"]["OPTIONS"] = {"location": str(MEDIA_ROOT / "originals")}
STORAGES["working"]["OPTIONS"] = {"location": str(MEDIA_ROOT / "working")}
