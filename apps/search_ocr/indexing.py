from django.contrib.postgres.search import SearchVector

from apps.documents.models import NormativeDocument

from .read_models import DocumentSearchIndex

SEARCH_CONFIG = "russian"


def refresh_document_search_index(document_id) -> None:
    """Upsert one read-model row and recompute persisted FTS vectors."""
    try:
        document = NormativeDocument.objects.get(pk=document_id)
    except NormativeDocument.DoesNotExist:
        DocumentSearchIndex.objects.filter(document_id=document_id).delete()
        return

    DocumentSearchIndex.objects.update_or_create(
        document_id=document.pk,
        defaults={
            "reg_number": document.reg_number,
            "reg_date": document.reg_date,
            "status": document.status,
            "access_level": document.access_level,
            "title": document.title,
            "summary": document.summary,
            "ocr_body": document.ocr_body,
        },
    )
    DocumentSearchIndex.objects.filter(document_id=document.pk).update(
        title_vector=SearchVector("title", config=SEARCH_CONFIG),
        summary_vector=SearchVector("summary", config=SEARCH_CONFIG),
        ocr_vector=SearchVector("ocr_body", config=SEARCH_CONFIG),
        search_vector=(
            SearchVector("title", config=SEARCH_CONFIG)
            + SearchVector("summary", config=SEARCH_CONFIG)
            + SearchVector("ocr_body", config=SEARCH_CONFIG)
        ),
    )


def rebuild_all_document_search_indexes() -> int:
    count = 0
    for document_id in NormativeDocument.objects.values_list("pk", flat=True).iterator(chunk_size=500):
        refresh_document_search_index(document_id)
        count += 1
    return count
