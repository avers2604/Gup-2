from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations, models
import django.db.models.deletion


def seed_index(apps, schema_editor):
    Document = apps.get_model("documents", "NormativeDocument")
    Index = apps.get_model("search_ocr", "DocumentSearchIndex")
    rows = []
    for document in Document.objects.all().iterator(chunk_size=500):
        rows.append(Index(
            document_id=document.pk,
            reg_number=document.reg_number,
            reg_date=document.reg_date,
            status=document.status,
            access_level=document.access_level,
            title=document.title,
            summary=document.summary,
            ocr_body=document.ocr_body,
        ))
        if len(rows) >= 500:
            Index.objects.bulk_create(rows, ignore_conflicts=True)
            rows.clear()
    if rows:
        Index.objects.bulk_create(rows, ignore_conflicts=True)


def clear_index(apps, schema_editor):
    apps.get_model("search_ocr", "DocumentSearchIndex").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0006_normativedocument_ocr_category_and_more"),
        ("search_ocr", "0004_remove_thesaurusambiguity_candidates_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSearchIndex",
            fields=[
                ("document", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True,
                    related_name="search_index",
                    serialize=False,
                    to="documents.normativedocument",
                )),
                ("reg_number", models.CharField(max_length=64)),
                ("reg_date", models.DateField()),
                ("status", models.CharField(max_length=16)),
                ("access_level", models.CharField(max_length=16)),
                ("title", models.CharField(max_length=500)),
                ("summary", models.TextField(blank=True)),
                ("ocr_body", models.TextField(blank=True)),
                ("title_vector", SearchVectorField(null=True)),
                ("summary_vector", SearchVectorField(null=True)),
                ("ocr_vector", SearchVectorField(null=True)),
                ("search_vector", SearchVectorField(null=True)),
                ("indexed_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Поисковый индекс документа",
                "verbose_name_plural": "Поисковый индекс документов",
                "indexes": [
                    GinIndex(fields=["search_vector"], name="search_doc_vector_gin"),
                    models.Index(fields=["status", "access_level"], name="search_doc_access_idx"),
                    models.Index(fields=["reg_date"], name="search_doc_date_idx"),
                ],
            },
        ),
        migrations.RunPython(seed_index, clear_index),
        migrations.RunSQL(
            sql="""
                UPDATE search_ocr_documentsearchindex
                SET title_vector = to_tsvector('russian', coalesce(title, '')),
                    summary_vector = to_tsvector('russian', coalesce(summary, '')),
                    ocr_vector = to_tsvector('russian', coalesce(ocr_body, '')),
                    search_vector =
                        to_tsvector('russian', coalesce(title, '')) ||
                        to_tsvector('russian', coalesce(summary, '')) ||
                        to_tsvector('russian', coalesce(ocr_body, ''))
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
