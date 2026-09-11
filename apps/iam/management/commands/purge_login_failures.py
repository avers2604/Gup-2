from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.business_metrics import LOGIN_FAILURE_PURGE_SUCCESSES, increment_counter
from apps.iam.models import LoginFailure


class Command(BaseCommand):
    help = "Удалить устаревшие operational LoginFailure; WORM AuditLog не затрагивается."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=24,
            help="Удалить записи старше N часов (по умолчанию 24; минимум 1).",
        )

    def handle(self, *args, **options):
        hours = options["older_than_hours"]
        if hours < 1:
            raise CommandError("--older-than-hours должен быть >= 1")

        cutoff = timezone.now() - timedelta(hours=hours)
        # Delete + heartbeat are one DB transaction: monitoring may only report
        # a successful purge after the retention mutation itself committed.
        with transaction.atomic():
            deleted, _ = LoginFailure.objects.filter(created_at__lt=cutoff).delete()
            increment_counter(LOGIN_FAILURE_PURGE_SUCCESSES)

        self.stdout.write(
            self.style.SUCCESS(
                f"Удалено operational LoginFailure: {deleted}; cutoff={cutoff.isoformat()}"
            )
        )
