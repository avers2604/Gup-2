from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import QueueDrillProbe
from apps.core.tasks import queue_acceptance_probe


class Command(BaseCommand):
    help = "Stage 4 Celery/Redis failure drill: enqueue or verify idempotent probe tasks."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        start = sub.add_parser("start")
        start.add_argument("--run-id", required=True)
        start.add_argument("--count", type=int, default=20)
        start.add_argument("--sleep-seconds", type=int, default=15)

        verify = sub.add_parser("verify")
        verify.add_argument("--run-id", required=True)
        verify.add_argument("--expected-count", type=int, required=True)

        status = sub.add_parser("status")
        status.add_argument("--run-id", required=True)

    def handle(self, *args, **options):
        action = options["action"]
        if action == "start":
            self._start(options)
        elif action == "verify":
            self._verify(options)
        else:
            self._status(options["run_id"])

    def _start(self, options):
        run_id = options["run_id"]
        count = options["count"]
        sleep_seconds = options["sleep_seconds"]
        if count < 2:
            raise CommandError("--count must be >= 2")
        if sleep_seconds < 1:
            raise CommandError("--sleep-seconds must be >= 1 so failure can be injected under load")
        if QueueDrillProbe.objects.filter(run_id=run_id).exists():
            raise CommandError(f"run_id already exists: {run_id}")

        probes = [QueueDrillProbe(run_id=run_id, sequence=i) for i in range(1, count + 1)]
        QueueDrillProbe.objects.bulk_create(probes)
        probes = list(QueueDrillProbe.objects.filter(run_id=run_id).order_by("sequence"))

        for probe in probes:
            result = queue_acceptance_probe.apply_async(args=[probe.pk, sleep_seconds])
            QueueDrillProbe.objects.filter(pk=probe.pk).update(task_id=result.id or "")

        self.stdout.write(
            self.style.WARNING(
                f"Enqueued {count} probes for {run_id}. While tasks are running, kill -9 a Celery worker "
                "or Redis according to the Stage 4 runbook, restart it, then run queue_drill verify."
            )
        )

    def _verify(self, options):
        run_id = options["run_id"]
        expected = options["expected_count"]
        rows = list(QueueDrillProbe.objects.filter(run_id=run_id))
        total = len(rows)
        completed = sum(1 for row in rows if row.completion_count == 1 and row.completed_at is not None)
        duplicate_effects = sum(1 for row in rows if row.completion_count != 1)
        redelivered = sum(1 for row in rows if row.delivery_count > 1)
        never_delivered = sum(1 for row in rows if row.delivery_count == 0)

        self.stdout.write(
            f"run_id={run_id} total={total} completed={completed} "
            f"redelivered={redelivered} never_delivered={never_delivered} "
            f"duplicate_effects={duplicate_effects}"
        )
        if total != expected:
            raise CommandError(f"probe count mismatch: expected={expected} actual={total}")
        if completed != expected or duplicate_effects or never_delivered:
            raise CommandError("queue drill failed: task loss or duplicate business completion detected")
        self.stdout.write(self.style.SUCCESS("Queue drill PASS: all tasks completed exactly once."))

    def _status(self, run_id):
        rows = QueueDrillProbe.objects.filter(run_id=run_id)
        self.stdout.write(
            f"run_id={run_id} total={rows.count()} completed={rows.filter(completion_count=1).count()} "
            f"pending={rows.filter(completion_count=0).count()}"
        )
