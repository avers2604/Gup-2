from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models


class DocumentSearchIndex(models.Model):
    """Denormalized read-model used only by Smart Search.

    The write-model remains ``documents.NormativeDocument``. This table stores
    the fields needed for access filtering, ordering and ranking so expensive
    ``to_tsvector`` work is done on writes/reindexing instead of every search.
    """

    document = models.OneToOneField(
        "documents.NormativeDocument",
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="search_index",
    )
    reg_number = models.CharField(max_length=64)
    reg_date = models.DateField()
    status = models.CharField(max_length=16)
    access_level = models.CharField(max_length=16)
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True)
    ocr_body = models.TextField(blank=True)

    title_vector = SearchVectorField(null=True)
    summary_vector = SearchVectorField(null=True)
    ocr_vector = SearchVectorField(null=True)
    search_vector = SearchVectorField(null=True)
    indexed_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Поисковый индекс документа"
        verbose_name_plural = "Поисковый индекс документов"
        indexes = [
            GinIndex(fields=["search_vector"], name="search_doc_vector_gin"),
            models.Index(fields=["status", "access_level"], name="search_doc_access_idx"),
            models.Index(fields=["reg_date"], name="search_doc_date_idx"),
        ]

    def __str__(self):
        return f"{self.reg_number} · {self.indexed_at:%Y-%m-%d %H:%M}"
