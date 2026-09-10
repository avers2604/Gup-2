"""At-least-once task delivery; consumers must be idempotent."""
import logging
from datetime import timedelta

from django.utils.module_loading import import_string
from django.db import transaction
from django.utils import timezone

from .models import TaskOutbox

logger = logging.getLogger(__name__)


def enqueue(task_name, args):
    entry = TaskOutbox.objects.create(task_name=task_name, args=args)
    transaction.on_commit(lambda: dispatch(entry.pk), robust=True)
    return entry


def dispatch(entry_id):
    with transaction.atomic():
        entry = TaskOutbox.objects.select_for_update(skip_locked=True).filter(
            pk=entry_id, delivered_at__isnull=True, next_attempt_at__lte=timezone.now(),
        ).first()
        if entry is None:
            return False
        entry.attempts += 1
        try:
            import_string(entry.task_name).apply_async(
                args=entry.args, task_id=str(entry.pk), retry=False,
            )
        except Exception as exc:
            entry.last_error = type(exc).__name__
            entry.next_attempt_at = timezone.now() + timedelta(seconds=min(300, 2 ** min(entry.attempts, 8)))
            logger.warning("Task delivery deferred: %s (%s)", entry.pk, type(exc).__name__)
        else:
            entry.delivered_at = timezone.now()
            entry.last_error = ""
        entry.save(update_fields=["attempts", "delivered_at", "next_attempt_at", "last_error"])
        return entry.delivered_at is not None


def dispatch_pending(limit=100):
    ids = list(TaskOutbox.objects.filter(delivered_at__isnull=True,
        next_attempt_at__lte=timezone.now()).order_by("created_at").values_list("pk", flat=True)[:limit])
    return sum(dispatch(pk) for pk in ids)
