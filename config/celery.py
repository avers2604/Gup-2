"""Celery application for background jobs and Stage 4 queue recovery drills."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("bz_get")
app.config_from_object("django.conf:settings", namespace="CELERY")

# Redis keeps unacknowledged messages invisible until visibility_timeout.
# The value must stay ABOVE the longest task time limit (OCR hard limit=600s)
# to avoid duplicate delivery during a healthy long-running OCR. 900s gives a
# bounded 15-minute recovery window for whole-worker SIGKILL drills.
#
# Do not mutate app.conf.broker_transport_options in place: after
# config_from_object() that object may be the exact dict exported by Django
# settings. Celery's namespaced ConfigurationView resolves the prefixed key
# first, so the runtime override itself must also be stored under the prefixed
# key; writing only the lowercase key is shadowed by Django settings.
redis_visibility_timeout = int(os.environ.get("CELERY_REDIS_VISIBILITY_TIMEOUT", "900"))
if redis_visibility_timeout <= 600:
    raise ValueError("CELERY_REDIS_VISIBILITY_TIMEOUT must be > OCR hard time limit (600s)")
broker_transport_options = dict(app.conf.broker_transport_options or {})
broker_transport_options["visibility_timeout"] = redis_visibility_timeout
app.conf["CELERY_BROKER_TRANSPORT_OPTIONS"] = broker_transport_options
# Keep only one unacknowledged task reserved per worker process. This reduces
# the recovery blast radius and makes kill/restart evidence easier to audit.
app.conf.worker_prefetch_multiplier = 1

app.autodiscover_tasks()
