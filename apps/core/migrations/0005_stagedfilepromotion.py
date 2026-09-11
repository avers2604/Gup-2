# Generated manually for Stage 4 WORM staging/promotion hardening.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_taskoutbox"),
    ]

    operations = [
        migrations.CreateModel(
            name="StagedFilePromotion",
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
                ("model_label", models.CharField(max_length=120)),
                ("object_id", models.CharField(max_length=64)),
                ("field_name", models.CharField(max_length=64)),
                ("staging_name", models.TextField(unique=True)),
                ("destination_name", models.TextField()),
                ("lock_mode", models.CharField(blank=True, max_length=16)),
                ("retain_until", models.DateTimeField(blank=True, null=True)),
                ("legal_hold", models.BooleanField(default=False)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, max_length=500)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["completed_at", "created_at"],
                        name="staged_promotion_pending_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("model_label", "object_id", "field_name", "destination_name"),
                        name="unique_staged_file_promotion",
                    ),
                ],
            },
        ),
    ]
