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


def _truncate_read_model() -> None:
    """Start an acceptance rebuild from a physically empty read-model table.

    This intentionally uses TRUNCATE rather than row-by-row DELETE: the Stage 4
    criterion measures rebuilding the index from zero, not normal online
    maintenance. Callers must run cold mode outside a surrounding transaction.
    """
    index_table, _ = _table_names()
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE {index_table}")


def rebuild_document_search_index(
    *,
    batch_size: int = 1000,
    require_count: int | None = None,
    cold: bool = False,
) -> dict:
    """Rebuild the persisted Smart Search read model.

    `cold=False` is the production-safe online refresh: existing rows remain
    queryable while batches are UPSERTed. `cold=True` is destructive acceptance
    mode: require an exact corpus size, empty the read model first, then rebuild
    every vector from source-of-truth documents. The elapsed measurement includes
    the truncate operation.
    """
    from apps.documents.models import NormativeDocument

    if batch_size < 1 or batch_size > 5000:
        raise ValueError("batch_size must be in 1..5000")
    if cold and require_count is None:
        raise ValueError("cold rebuild requires --require-count to guard destructive execution")
    if cold and connection.in_atomic_block:
        raise ValueError("cold rebuild must run outside an existing database transaction")

    total = NormativeDocument.objects.count()
    if require_count is not None and total != require_count:
        raise ValueError(f"document corpus must contain exactly {require_count}, actual={total}")

    started = time.monotonic()
    if cold:
        _truncate_read_model()

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
            locked_ids = list(
                NormativeDocument.objects.select_for_update()
                .filter(pk__in=ids)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            rebuilt += _upsert_ids(locked_ids)
        last_pk = ids[-1]

    return {
        "documents": rebuilt,
        "elapsed_seconds": time.monotonic() - started,
        "mode": "cold" if cold else "online",
    }
