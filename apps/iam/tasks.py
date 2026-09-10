from celery import shared_task
from django.core.management import call_command


@shared_task
def cleanup_expired_tokens():
    # flushexpiredtokens — встроенная команда rest_framework_simplejwt.
    # token_blacklist: удаляет из БД записи blacklist/outstanding токенов,
    # чей срок (REFRESH_TOKEN_LIFETIME) уже истёк — сам список не растёт
    # бесконечно, даже пока beat не запущен и очистка вызывается вручную.
    call_command("flushexpiredtokens")
    from datetime import timedelta
    from django.utils import timezone
    from .models import UsedLoginTicket
    from apps.core.models import TaskOutbox
    UsedLoginTicket.objects.filter(expires_at__lte=timezone.now()).delete()
    TaskOutbox.objects.filter(delivered_at__lt=timezone.now() - timedelta(days=7)).delete()
