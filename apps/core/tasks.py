from __future__ import annotations

import time

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import QueueDrillProbe


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def queue_acceptance_probe(self, probe_id: int, sleep_seconds: int = 15):
    """Idempotent task used only for Stage 4 Celery/Redis crash drills.

    A killed worker may cause the same message to be delivered again. Every
    delivery is counted, but the durable completion side effect is protected by
    a row lock and can be committed only once.
    """
    QueueDrillProbe.objects.filter(pk=probe_id).update(delivery_count=F("delivery_count") + 1)
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    with transaction.atomic():
        probe = QueueDrillProbe.objects.select_for_update().get(pk=probe_id)
        if probe.completion_count:
            return {
                "probe_id": probe.pk,
                "task_id": self.request.id,
                "duplicate_delivery_suppressed": True,
            }
        probe.completion_count = 1
        probe.completed_at = timezone.now()
        probe.save(update_fields=["completion_count", "completed_at"])

    return {
        "probe_id": probe_id,
        "task_id": self.request.id,
        "duplicate_delivery_suppressed": False,
    }


@shared_task
def dispatch_task_outbox():
    from .outbox import dispatch_pending
    return dispatch_pending()


@shared_task(
    bind=True,
    max_retries=100,
    acks_late=True,
    reject_on_worker_lost=True,
    track_started=True,
)
def promote_staged_file(self, promotion_id: str):
    """Promote one mutable staging object into Object-Locked originals.

    The operation is idempotent: the destination carries the durable
    promotion UUID in object metadata, so worker loss after S3 copy but before
    PostgreSQL commit does not create another immutable version on retry.
    """
    from .staged_files import promote_staged_file_once, record_promotion_error

    try:
        digest = promote_staged_file_once(promotion_id)
    except Exception as exc:
        record_promotion_error(promotion_id, exc)
        delay = min(300, 2 ** min(self.request.retries + 1, 8))
        raise self.retry(exc=exc, countdown=delay)
    return {"promotion_id": promotion_id, "sha256": digest}
