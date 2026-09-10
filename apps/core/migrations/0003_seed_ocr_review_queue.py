from django.db import migrations


def seed_review_queue(apps, schema_editor):
    NormativeDocument = apps.get_model("documents", "NormativeDocument")
    OcrReviewQueueEntry = apps.get_model("core", "OcrReviewQueueEntry")

    rows = []
    for document in NormativeDocument.objects.filter(ocr_status="needs_review").iterator(chunk_size=1000):
        rows.append(
            OcrReviewQueueEntry(
                document_id=document.pk,
                required_at=document.updated_at or document.created_at,
            )
        )
        if len(rows) >= 1000:
            OcrReviewQueueEntry.objects.bulk_create(rows, ignore_conflicts=True)
            rows.clear()
    if rows:
        OcrReviewQueueEntry.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_ocr_review_queue_entry"),
        ("documents", "0006_normativedocument_ocr_category_and_more"),
    ]

    operations = [migrations.RunPython(seed_review_queue, migrations.RunPython.noop)]
