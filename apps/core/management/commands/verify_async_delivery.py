"""CI smoke test against real Redis and an independently running worker."""
import time
import uuid
from django.core.cache.backends.redis import RedisCache
from django.core.management.base import BaseCommand, CommandError
from apps.core.models import QueueDrillProbe, TaskOutbox
from apps.core.outbox import dispatch_pending


class Command(BaseCommand):
    def handle(self, *args, **options):
        from django.conf import settings
        from apps.core.tasks import queue_acceptance_probe
        key = "ci-cache-" + uuid.uuid4().hex
        cache1 = RedisCache(settings.CELERY_BROKER_URL, {})
        cache2 = RedisCache(settings.CELERY_BROKER_URL, {})
        try:
            if not cache1.add(key, 1, 60) or cache2.incr(key) != 2 or cache1.get(key) != 2:
                raise CommandError("Shared atomic counter failed")
        finally:
            cache1.delete(key)
        probe = QueueDrillProbe.objects.create(run_id="ci-" + uuid.uuid4().hex, sequence=1)
        entry = TaskOutbox.objects.create(task_name="apps.core.tasks.queue_acceptance_probe", args=[probe.pk, 0])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            dispatch_pending()
            probe.refresh_from_db()
            if probe.completion_count == 1:
                break
            time.sleep(0.5)
        else:
            raise CommandError("Worker did not complete durable outbox task")
        queue_acceptance_probe.apply_async(args=[probe.pk, 0], task_id=str(entry.pk))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            probe.refresh_from_db()
            if probe.delivery_count >= 2:
                break
            time.sleep(0.5)
        if probe.delivery_count < 2 or probe.completion_count != 1:
            raise CommandError("Redelivery did not preserve exactly one business effect")
        self.stdout.write(self.style.SUCCESS("Redis counters, outbox delivery and worker redelivery PASS"))
