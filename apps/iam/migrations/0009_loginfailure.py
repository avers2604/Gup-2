# Generated manually for Stage 4 P2 IAM lockout projection.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("iam", "0008_usedloginticket_user_auth_version_and_more"),
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
    ]
