import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0006_normativedocument_ocr_category_and_more"),
        ("search_ocr", "0004_remove_thesaurusambiguity_candidates_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSearchIndex",
            fields=[
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="search_read_model",
                        serialize=False,
                        to="documents.normativedocument",
                    ),
                ),
                ("title_vector", django.contrib.postgres.search.SearchVectorField()),
                ("summary_vector", django.contrib.postgres.search.SearchVectorField()),
                ("ocr_vector", django.contrib.postgres.search.SearchVectorField()),
                ("combined_vector", django.contrib.postgres.search.SearchVectorField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["combined_vector"], name="search_doc_combined_gin"
                    )
                ]
            },
        ),
    ]
