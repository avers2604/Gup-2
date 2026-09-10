from __future__ import annotations

from celery import shared_task

from .search_index import refresh_document_index


@shared_task(
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
    reject_on_worker_lost=True,
)
def refresh_document_search_index(document_id: str):
    return refresh_document_index(document_id)
