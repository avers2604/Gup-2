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
# Plain reassignment (`app.conf.broker_transport_options = {...}`) is a NO-OP
# here: celery.app.utils.Settings resolves `broker_transport_options` against
# the object passed to config_from_object() first, so a later attribute
# assignment is silently discarded on read and the real runtime value falls
# back to kombu's built-in default of 3600s — four times the documented
# recovery window, and enough to make the guard below validate a number that
# is never actually applied. Reproduced on the lab rehearsal
# (docs/STAGE4_LAB_REHEARSAL.md, Ф-8): four probes killed mid-task stayed
# unacked in Redis and were never redelivered even after waiting the
# documented 900s, because the broker was actually running with a 3600s
# timeout. `.update()` mutates the existing dict in place instead of
# replacing it, which does take effect — verified with a live worker
# (`channel.qos.visibility_timeout` read back 900, not 3600).
redis_visibility_timeout = int(os.environ.get("CELERY_REDIS_VISIBILITY_TIMEOUT", "900"))
if redis_visibility_timeout <= 600:
    raise ValueError("CELERY_REDIS_VISIBILITY_TIMEOUT must be > OCR hard time limit (600s)")
app.conf.broker_transport_options.update({"visibility_timeout": redis_visibility_timeout})
# Keep only one unacknowledged task reserved per worker process. This reduces
# the recovery blast radius and makes kill/restart evidence easier to audit.
app.conf.worker_prefetch_multiplier = 1

app.autodiscover_tasks()
