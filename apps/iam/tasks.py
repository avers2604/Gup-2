"""Периодическая очистка чёрного списка JWT (rest_framework_simplejwt.
token_blacklist) — по запросу ревью анти-фрода. Честная граница: запись
CELERY_BEAT_SCHEDULE в config/settings/base.py задана, но ни один процесс
celery beat в проекте не запускается — задача сейчас вызывается только
вручную/из тестов, не по расписанию (см. STACK.md)."""
from celery import shared_task
from django.core.management import call_command


@shared_task
def cleanup_expired_tokens():
    # flushexpiredtokens — встроенная команда rest_framework_simplejwt.
    # token_blacklist: удаляет из БД записи blacklist/outstanding токенов,
    # чей срок (REFRESH_TOKEN_LIFETIME) уже истёк — сам список не растёт
    # бесконечно, даже пока beat не запущен и очистка вызывается вручную.
    call_command("flushexpiredtokens")
