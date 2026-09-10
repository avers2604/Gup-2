from celery import shared_task

from .indexing import rebuild_all_document_search_indexes, refresh_document_search_index


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def refresh_document_search_index_task(self, document_id: str) -> None:
    refresh_document_search_index(document_id)


@shared_task
def rebuild_document_search_index_task() -> int:
    return rebuild_all_document_search_indexes()
