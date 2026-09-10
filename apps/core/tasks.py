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
