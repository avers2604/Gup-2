from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.documents.models import NormativeDocument

from .search_index import refresh_document_index
from .tasks import refresh_document_search_index


@receiver(post_save, sender=NormativeDocument, dispatch_uid="search_ocr.refresh_document_index")
def schedule_search_index_refresh(sender, instance, **kwargs):
    document_id = str(instance.pk)
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        # Django TestCase wraps each test in a transaction which never reaches a
        # real commit while assertions run. Keep eager mode deterministic.
        refresh_document_index(document_id)
        return
    transaction.on_commit(lambda: refresh_document_search_index.delay(document_id))
