"""Celery-приложение (очереди асинхронных задач, конвейер OCR — Этап 3).

Брокер — Redis (CELERY_BROKER_URL, config/settings/base.py), уже был
предусмотрен в стеке (docker-compose.yml, .env.example), но не
использовался кодом до этой партии.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("bz_get")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
