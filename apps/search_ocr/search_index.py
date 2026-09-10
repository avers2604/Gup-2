from __future__ import annotations

import time

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import connection, models, transaction


class DocumentSearchIndex(models.Model):
    """CQRS-style read model for Smart Search.

    Source of truth remains documents.NormativeDocument. The read model stores
    precomputed Russian FTS vectors and can be truncated/rebuilt independently.
    """

    document = models.OneToOneField(
        "documents.NormativeDocument",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="search_read_model",
    )
    title_vector = SearchVectorField()
    summary_vector = SearchVectorField()
    ocr_vector = SearchVectorField()
    combined_vector = SearchVectorField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [GinIndex(fields=["combined_vector"], name="search_doc_combined_gin")]


UPSERT_SELECT = """
INSERT INTO {index_table}
    (document_id, title_vector, summary_vector, ocr_vector, combined_vector, updated_at)
SELECT
    d.id,
    to_tsvector('russian', coalesce(d.title, '')),
    to_tsvector('russian', coalesce(d.summary, '')),
    to_tsvector('russian', coalesce(d.ocr_body, '')),
    to_tsvector(
        'russian',
        concat_ws(' ', coalesce(d.title, ''), coalesce(d.summary, ''), coalesce(d.ocr_body, ''))
    ),
    CURRENT_TIMESTAMP
FROM {document_table} d
WHERE d.id IN ({{placeholders}})
ON CONFLICT (document_id) DO UPDATE SET
    title_vector = EXCLUDED.title_vector,
    summary_vector = EXCLUDED.summary_vector,
    ocr_vector = EXCLUDED.ocr_vector,
    combined_vector = EXCLUDED.combined_vector,
    updated_at = EXCLUDED.updated_at
"""


def _table_names() -> tuple[str, str]:
    from apps.documents.models import NormativeDocument

    quote = connection.ops.quote_name
    return quote(DocumentSearchIndex._meta.db_table), quote(NormativeDocument._meta.db_table)


def _upsert_ids(document_ids) -> int:
    ids = list(document_ids)
    if not ids:
        return 0
    index_table, document_table = _table_names()
    placeholders = ", ".join(["%s"] * len(ids))
    sql = UPSERT_SELECT.format(index_table=index_table, document_table=document_table).format(
        placeholders=placeholders
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, ids)
    return len(ids)


def refresh_document_index(document_id) -> bool:
    from apps.documents.models import NormativeDocument
    with transaction.atomic():
        if not NormativeDocument.objects.select_for_update().filter(pk=document_id).exists():
            return False
        return _upsert_ids([document_id]) == 1


def rebuild_document_search_index(*, batch_size: int = 1000, require_count: int | None = None) -> dict:
    """Refresh in bounded transactions while readers retain the existing index."""
    from apps.documents.models import NormativeDocument

    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch_size must be in 1..5000")

    total = NormativeDocument.objects.count()
    if require_count is not None and total != require_count:
        raise ValueError(f"document corpus must contain exactly {require_count}, actual={total}")
    started = time.monotonic()
    rebuilt = 0
    last_pk = None
    while True:
        batch = NormativeDocument.objects.order_by("pk")
        if last_pk is not None:
            batch = batch.filter(pk__gt=last_pk)
        ids = list(batch.values_list("pk", flat=True)[:batch_size])
        if not ids:
            break
        with transaction.atomic():
            # Serialize refresh with source writes to avoid stale upserts.
            locked_ids = list(NormativeDocument.objects.select_for_update().filter(
                pk__in=ids).order_by("pk").values_list("pk", flat=True))
            rebuilt += _upsert_ids(locked_ids)
        last_pk = ids[-1]
    return {"documents": rebuilt, "elapsed_seconds": time.monotonic() - started}
