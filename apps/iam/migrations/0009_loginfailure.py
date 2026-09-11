# Generated manually for Stage 4 P2 IAM lockout projection.

import uuid
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_active_lockout_window(apps, schema_editor):
    """Preserve failures that can still participate in the 15-minute lockout."""
    AuditLog = apps.get_model("audit", "AuditLog")
    LoginFailure = apps.get_model("iam", "LoginFailure")
    cutoff = timezone.now() - timedelta(minutes=15)

    failures = AuditLog.objects.filter(
        event_type="session.login_failed",
        created_at__gte=cutoff,
    ).order_by("created_at")

    for audit in failures.iterator(chunk_size=500):
        details = audit.details or {}
        row = LoginFailure.objects.create(
            personnel_number=(audit.actor_personnel_number or "")[:32],
            ip_address=str(details.get("ip_address") or "")[:45],
            stage=str(details.get("stage") or "legacy")[:32],
            reason=str(details.get("reason") or "legacy_audit")[:64],
        )
        # auto_now_add writes migration time; restore the original failure time
        # so Retry-After and sliding-window expiry do not get artificially reset.
        LoginFailure.objects.filter(pk=row.pk).update(created_at=audit.created_at)


class Migration(migrations.Migration):

    dependencies = [
        ("iam", "0008_usedloginticket_user_auth_version_and_more"),
        ("audit", "0015_alter_auditlog_event_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoginFailure",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("personnel_number", models.CharField(blank=True, max_length=32)),
                ("ip_address", models.CharField(blank=True, max_length=45)),
                ("stage", models.CharField(max_length=32)),
                ("reason", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["personnel_number", "created_at"],
                        name="iam_login_fail_person_idx",
                    ),
                    models.Index(
                        fields=["ip_address", "created_at"],
                        name="iam_login_fail_ip_idx",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            backfill_active_lockout_window,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
