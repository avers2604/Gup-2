from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_business_metrics_and_queue_drill")]

    operations = [
        migrations.CreateModel(
            name="OcrReviewQueueEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_id", models.UUIDField(unique=True)),
                ("required_at", models.DateTimeField()),
            ],
            options={"ordering": ["required_at"]},
        ),
        migrations.AddIndex(
            model_name="ocrreviewqueueentry",
            index=models.Index(fields=["required_at"], name="core_ocrrev_require_4d2cbf_idx"),
        ),
    ]
