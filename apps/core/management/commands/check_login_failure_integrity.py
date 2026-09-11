from collections import Counter
from datetime import timedelta

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Read-only comparison of IAM LoginFailure operational projection "
        "against WORM session.login_failed audit events."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--window-minutes",
            type=int,
            default=15,
            help="Sliding interval to compare (default: 15 minutes).",
        )
        parser.add_argument(
            "--settle-seconds",
            type=int,
            default=5,
            help=(
                "Exclude this many seconds at both interval edges to avoid "
                "false positives from in-flight writes/boundary skew (default: 5)."
            ),
        )
        parser.add_argument(
            "--sample-limit",
            type=int,
            default=10,
            help="Maximum mismatched keys printed per side (default: 10).",
        )

    def handle(self, *args, **options):
        window_minutes = options["window_minutes"]
        settle_seconds = options["settle_seconds"]
        sample_limit = options["sample_limit"]

        if window_minutes < 1:
            raise CommandError("--window-minutes должен быть >= 1")
        if settle_seconds < 0:
            raise CommandError("--settle-seconds должен быть >= 0")
        if sample_limit < 1:
            raise CommandError("--sample-limit должен быть >= 1")

        window_seconds = window_minutes * 60
        if settle_seconds * 2 >= window_seconds:
            raise CommandError(
                "--settle-seconds должен оставлять ненулевой интервал сравнения"
            )

        LoginFailure = apps.get_model("iam", "LoginFailure")
        AuditLog = apps.get_model("audit", "AuditLog")

        now = timezone.now()
        start = now - timedelta(minutes=window_minutes) + timedelta(seconds=settle_seconds)
        end = now - timedelta(seconds=settle_seconds)

        projection_counter = Counter(
            (
                row.personnel_number,
                row.ip_address,
                row.stage,
                row.reason,
            )
            for row in LoginFailure.objects.filter(
                created_at__gte=start,
                created_at__lte=end,
            ).iterator(chunk_size=1000)
        )

        def audit_key(row):
            details = row.details or {}
            # Same normalization/truncation contract as iam.0009 backfill.
            return (
                (row.actor_personnel_number or "")[:32],
                str(details.get("ip_address") or "")[:45],
                str(details.get("stage") or "legacy")[:32],
                str(details.get("reason") or "legacy_audit")[:64],
            )

        audit_counter = Counter(
            audit_key(row)
            for row in AuditLog.objects.filter(
                event_type="session.login_failed",
                created_at__gte=start,
                created_at__lte=end,
            ).iterator(chunk_size=1000)
        )

        projection_without_audit = projection_counter - audit_counter
        audit_without_projection = audit_counter - projection_counter
        projection_missing_count = sum(projection_without_audit.values())
        audit_missing_count = sum(audit_without_projection.values())

        if projection_missing_count or audit_missing_count:
            self.stdout.write(
                "FAIL login-failure integrity: "
                f"projection_without_audit={projection_missing_count}, "
                f"audit_without_projection={audit_missing_count}"
            )
            self._print_samples(
                "projection_without_audit",
                projection_without_audit,
                sample_limit,
            )
            self._print_samples(
                "audit_without_projection",
                audit_without_projection,
                sample_limit,
            )
            raise CommandError(
                "LoginFailure/AuditLog integrity mismatch: "
                f"projection_without_audit={projection_missing_count}, "
                f"audit_without_projection={audit_missing_count}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "PASS login-failure integrity: "
                f"projection={sum(projection_counter.values())}, "
                f"audit={sum(audit_counter.values())}, "
                f"window={window_minutes}m, settle={settle_seconds}s"
            )
        )

    def _print_samples(self, label, counter, sample_limit):
        for index, (key, count) in enumerate(counter.items()):
            if index >= sample_limit:
                break
            personnel, ip_address, stage, reason = key
            self.stdout.write(
                f"{label} x{count}: personnel={personnel!r}, "
                f"ip={ip_address!r}, stage={stage!r}, reason={reason!r}"
            )
