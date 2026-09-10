from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.documents.models import NormativeDocument

from .tasks import refresh_document_search_index


@receiver(post_save, sender=NormativeDocument, dispatch_uid="search_ocr.refresh_document_index")
def schedule_search_index_refresh(sender, instance, **kwargs):
    document_id = str(instance.pk)
    transaction.on_commit(lambda: refresh_document_search_index.delay(document_id))
