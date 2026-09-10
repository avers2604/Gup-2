from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BusinessMetricCounter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True)),
                ("value", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="QueueDrillProbe",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.CharField(max_length=80)),
                ("sequence", models.PositiveIntegerField()),
                ("task_id", models.CharField(blank=True, max_length=255)),
                ("delivery_count", models.PositiveIntegerField(default=0)),
                ("completion_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["run_id", "sequence"]},
        ),
        migrations.AddConstraint(
            model_name="queuedrillprobe",
            constraint=models.UniqueConstraint(fields=("run_id", "sequence"), name="unique_queue_drill_probe"),
        ),
        migrations.AddIndex(
            model_name="queuedrillprobe",
            index=models.Index(fields=["run_id", "completed_at"], name="core_queue_run_id_9f8bd3_idx"),
        ),
    ]
