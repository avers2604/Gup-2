import logging

from celery import shared_task
from django.core.management import call_command

from apps.core.storage import working_storage

logger = logging.getLogger(__name__)


def _row_to_dict(row):
    return {
        "row_number": row.row_number,
        "tab_number": row.tab_number,
        "detail": row.detail,
    }


@shared_task(
    bind=True,
    max_retries=2,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_personnel_import_task(self, *, storage_name: str, actor_id: str | None = None) -> dict:
    """Обработать один staged-файл импорта и вернуть JSON-совместимый отчёт.

    Файл передаётся через рабочее хранилище (см. apps.iam.async_import), а
    в брокер уходит только его имя: гонять .xlsx целиком через Redis нельзя
    — это и предел размера сообщения, и лишняя копия персональных данных в
    брокере.
    """
    from .models import User
    from .services import import_personnel

    storage = working_storage()
    # Защита от повторной доставки (acks_late=True, тот же принцип, что у
    # _already_finalized() в apps/documents/tasks.py): staged-файл удаляется
    # только после отработки задачи, поэтому его отсутствие означает, что
    # эта задача уже выполнена. Повторный импорт того же файла не «просто
    # повтор» — он ещё раз перезапишет учётные записи и повторно поднимет
    # события смены роли в WORM-журнале.
    if not storage.exists(storage_name):
        logger.info("run_personnel_import_task: повторная доставка %r подавлена", storage_name)
        return {"duplicate": True, "storage_name": storage_name}

    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    cleanup_source = True
    try:
        with storage.open(storage_name, "rb") as stream:
            report = import_personnel(stream, actor=actor)
        return {
            "total": report.total,
            "created": len(report.successes),
            "updated": len(report.updates),
            "errors": len(report.errors),
            "error_rows": [_row_to_dict(row) for row in report.errors],
        }
    except OSError as exc:
        # Временный сбой хранилища повторяем. Staged-источник при этом НЕ
        # удаляем, чтобы следующая попытка прочитала ровно тот же файл.
        cleanup_source = False
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 30))
    finally:
        if cleanup_source and storage.exists(storage_name):
            storage.delete(storage_name)


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
